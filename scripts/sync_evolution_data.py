"""Fail-closed immutable GitHub evolution export for Adaptive Router v1.2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import router_core

REPOSITORY_URL = "https://github.com/williamxhero/codex-adaptive-router.git"
DEFAULT_REPOSITORY = (
    Path.home() / ".codex" / "state" / "codex-adaptive-router-evolution-repo"
)
DEFAULT_ROUTER_DATA_ROOT = Path.home() / ".codex" / "codex-adaptive-router"
DEFAULT_HOOK_DATA_ROOT = (
    Path.home() / ".codex" / "plugins" / "data" / "codex-adaptive-router-personal"
)
FORBIDDEN_KEYS = {
    "task",
    "prompt",
    "raw_prompt",
    "path",
    "source_path",
    "output",
    "tool_input",
    "tool_output",
    "assistant_message",
    "last_assistant_message",
    "transcript",
    "transcript_path",
    "code",
    "log",
    "logs",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "api_key",
    "private_key",
}
PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\|/(?:Users|home|data|workspace|tmp)/)", re.IGNORECASE
)
SECRET_PATTERN = re.compile(
    r"(?:s" r"k-[A-Za-z0-9_-]{16,}|g" r"h[opusr]_[A-Za-z0-9]{20,}|A" r"KIA[A-Z0-9]{16})"
)


class SyncError(RuntimeError):
    pass


MIGRATION_NAMESPACE = uuid.UUID("f77bf564-810a-4ea8-a6f9-0be79dfda401")


def _legacy_uuid(kind: str, value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(uuid.uuid5(MIGRATION_NAMESPACE, f"{kind}:{canonical}"))


def _legacy_identity(kind: str, value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"legacy-v1:{kind}:{canonical}".encode()).hexdigest()[:32]


def _legacy_sequence(record: dict[str, Any]) -> int:
    value = record.get("sequence")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return int(hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:8], 16) + 1


def _legacy_route_tuple(record: dict[str, Any]) -> tuple[str, str, str]:
    role = record.get("role") if record.get("role") in router_core.VALID_ROLES else "direct"
    profile = record.get("profile") if record.get("profile") in {"generic", "quant"} else "generic"
    config = router_core.load_profile(profile)["roles"][role]
    model = record.get("model") if record.get("model") in config["allowed_models"] else config["default_model"]
    effort = record.get("reasoning_effort")
    order = ["low", "medium", "high", "xhigh", "max", "ultra"]
    limits = config["effort"]
    allowed_efforts = order[order.index(limits["min"]) : order.index(limits["max"]) + 1]
    if effort not in allowed_efforts or effort in {"max", "ultra"}:
        effort = config["effort"]["default"]
    return role, model, effort


def migrate_event(record: dict[str, Any]) -> dict[str, Any]:
    """Return strict v2/v3 upload evidence without mutating source history."""
    if record.get("schema_version") in {2, 3}:
        value = json.loads(json.dumps(record))
        try:
            router_core.validate_evidence_event(value)
        except ValueError as error:
            raise SyncError(f"invalid v{record.get('schema_version')} evidence: {error}") from error
        assert_safe(value)
        return value
    if record.get("schema_version") not in {None, 1}:
        raise SyncError("unsupported evidence schema version")
    kind = record.get("type") if record.get("type") in {"route", "execution", "outcome"} else "execution"
    event_id = _legacy_uuid("event", record)
    route_id = str(record.get("route_id") or "")
    try:
        route_id = str(uuid.UUID(route_id))
    except ValueError:
        route_id = _legacy_uuid("route", record.get("route_id") or record)
    task_ref = _legacy_identity("task", record.get("task_ref") or route_id)
    base = {
        "schema_version": 2,
        "event_id": event_id,
        "sequence": _legacy_sequence(record),
        "created_at": record.get("created_at") if isinstance(record.get("created_at"), str) and len(record["created_at"]) <= 40 else "1970-01-01T00:00:00Z",
        "type": kind,
        "task_ref": task_ref,
        "route_id": route_id,
    }
    role, model, effort = _legacy_route_tuple(record)
    if kind == "route":
        task_class = record.get("task_class") if record.get("task_class") in router_core.FEATURE_VALUES["cognitive_type"] else "direct"
        value = {
            **base,
            "task_fingerprint": _legacy_identity("fingerprint", record.get("task_fingerprint") or task_ref),
            "profile": record.get("profile") if record.get("profile") in {"generic", "quant"} else "generic",
            "task_class": task_class,
            "role": role,
            "model": model,
            "reasoning_effort": effort,
            "confidence": float(record.get("confidence")) if isinstance(record.get("confidence"), (int, float)) and not isinstance(record.get("confidence"), bool) and 0 <= record["confidence"] <= 1 else 0.5,
            "decision_features": {
                "operation_mode": "answer", "scope": "bounded", "spec_state": "unknown",
                "reversibility": "reversible", "cognitive_type": task_class, "risk_domains": [],
                "workload": "medium", "user_constraints": [], "feature_source": "legacy_v1", "confidence": 0.5,
            },
            "constraints": {}, "policy_revision": 1, "shadow": None,
            "session": _legacy_identity("session", record.get("session") or event_id),
            "project": _legacy_identity("project", record.get("project") or "unknown"),
        }
    elif kind == "outcome":
        verified = record.get("verified") is True or record.get("quality_gate") == "passed"
        status = record.get("status") if record.get("status") in router_core.OUTCOME_STATUSES else ("verified" if verified else "completed")
        value = {
            **base, "status": status, "quality_gate": "passed" if verified else "provisional",
            "route_fit": record.get("route_fit") if record.get("route_fit") in router_core.ROUTE_FITS else "unknown",
            "verification_kinds": [], "confidence": 0.5, "evidence_source": "legacy_v1",
            "objective_verification": False, "user_confirmed": False, "replacement": None,
            "high_risk_regression": False, "retry_band": "unknown", "rework_band": "unknown",
            "tool_band": "unknown", "duration_band": "unknown", "token_band": "unknown", "cost_band": "unknown",
        }
    else:
        event = record.get("event") if record.get("event") in router_core.HOOK_EVENTS else "PostToolUse"
        value = {**base, "event": event, "tool_kind": "lifecycle" if event in {"SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"} else "local", "failed": bool(record.get("failed") is True)}
    try:
        router_core.validate_evidence_event(value)
    except ValueError as error:
        raise SyncError(f"v1 migration produced invalid evidence: {error}") from error
    assert_safe(value)
    return value


def run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args, cwd=cwd, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise SyncError(
            f"{' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout.strip()


def assert_safe(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise SyncError(f"refusing forbidden field: {key}")
            assert_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_safe(item)
    elif isinstance(value, str):
        if PATH_PATTERN.search(value):
            raise SyncError("refusing path-like value")
        if SECRET_PATTERN.search(value):
            raise SyncError("refusing secret-like value")
        if len(value) > 240:
            raise SyncError("refusing unbounded text value")


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    value = json.loads(path.read_text(encoding="utf-8"))
    assert_safe(value)
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise SyncError(f"invalid event line {number}: {error}") from error
        if not isinstance(value, dict):
            raise SyncError(f"event line {number} is not an object")
        assert_safe(value)
        result.append(value)
    return result


def ensure_clean_clone(repository: Path) -> None:
    if not repository.exists():
        repository.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", REPOSITORY_URL, str(repository))
    if not (repository / ".git").is_dir():
        raise SyncError("repository path is not a git clone")
    if run("git", "status", "--porcelain", cwd=repository):
        raise SyncError("dedicated evolution clone is dirty")
    run("git", "fetch", "origin", "main", cwd=repository)
    run("git", "checkout", "main", cwd=repository)
    run("git", "pull", "--ff-only", "origin", "main", cwd=repository)


def merged_records(*roots: Path) -> list[dict[str, Any]]:
    by_id = {}
    for root in roots:
        for raw_record in read_jsonl(root / "events" / "routing.jsonl"):
            record = migrate_event(raw_record)
            event_id = str(record["event_id"])
            if event_id in by_id and by_id[event_id] != record:
                raise SyncError(
                    f"duplicate event id with different payload: {event_id}"
                )
            by_id[event_id] = record
    return sorted(
        by_id.values(),
        key=lambda x: (int(x.get("sequence") or 0), str(x.get("event_id") or "")),
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, data: str) -> None:
    if path.exists():
        existing = path.read_bytes()
        if existing.decode("utf-8").replace("\r\n", "\n") != data:
            raise SyncError(f"immutable artifact differs: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8"))


def _write_lf(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8"))


def _normalise_metrics_event(record: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(record))
    if value.get("type") == "outcome":
        value.setdefault("model_fit", "unknown")
        value.setdefault("effort_fit", "unknown")
        value.setdefault("context_fit", "unknown")
        value.setdefault("tool_data_fit", "unknown")
        value.setdefault("failure_axis", "none")
        value.setdefault("result_signal", "unknown")
        value.setdefault("stage_source", "unknown")
    return value


def write_export(router_data_root: Path, hook_data_root: Path, repository: Path) -> int:
    records = merged_records(router_data_root, hook_data_root)
    target = repository / "evolution-data"
    target.mkdir(parents=True, exist_ok=True)
    _write_new(
        target / "legacy-v1.json",
        (PLUGIN_ROOT / "evolution-data" / "legacy-v1.json").read_text(encoding="utf-8"),
    )
    _write_new(
        target / "schemas" / "event-v2.schema.json",
        (PLUGIN_ROOT / "evolution-data" / "schemas" / "event-v2.schema.json").read_text(
            encoding="utf-8"
        ),
    )
    _write_new(
        target / "schemas" / "event-v3.schema.json",
        (PLUGIN_ROOT / "evolution-data" / "schemas" / "event-v3.schema.json").read_text(
            encoding="utf-8"
        ),
    )
    existing_ids = set()
    for batch in (
        (target / "batches").glob("*.jsonl") if (target / "batches").exists() else []
    ):
        for item in read_jsonl(batch):
            existing_ids.add(str(item.get("event_id")))
    fresh = [x for x in records if str(x.get("event_id")) not in existing_ids]
    previous = None
    latest = target / "latest.json"
    if latest.exists():
        previous = read_json(latest, {}).get("manifest_sha256")
    manifest_name = None
    if fresh:
        rendered_batch = "".join(
            json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in fresh
        )
        batch_id = hashlib.sha256(rendered_batch.encode("utf-8")).hexdigest()
        batch_name = f"batch-{batch_id}.jsonl"
        batch_path = target / "batches" / batch_name
        _write_new(batch_path, rendered_batch)
        manifest = {
            "schema_version": 3,
            "batch": batch_name,
            "count": len(fresh),
            "sha256": sha256(batch_path),
            "previous_manifest_sha256": previous,
            "created_at": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        manifest_name = f"manifest-{batch_id}.json"
        manifest_path = target / "manifests" / manifest_name
        _write_new(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        previous = sha256(manifest_path)
    current_policy = router_data_root / "policy" / "current.json"
    existing_revision = target / "policies" / "revision-1.json"
    policy = read_json(
        current_policy,
        read_json(existing_revision, router_core.default_policy()),
    )
    revision = int(policy.get("revision") or 1)
    _write_new(
        target / "policies" / f"revision-{revision}.json",
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    with tempfile.TemporaryDirectory() as directory:
        metrics_root = Path(directory)
        metrics_events = metrics_root / "events" / "routing.jsonl"
        metrics_events.parent.mkdir(parents=True)
        _write_lf(
            metrics_events,
            "".join(
                json.dumps(_normalise_metrics_event(item), sort_keys=True) + "\n"
                for item in records
            ),
        )
        metrics = router_core.router_metrics(metrics_root)
    rendered_metrics = json.dumps(metrics, indent=2, sort_keys=True) + "\n"
    metric_paths = (
        list((target / "metrics").glob("revision-*.json"))
        if (target / "metrics").exists()
        else []
    )
    latest_metric = (
        max(metric_paths, key=lambda path: int(path.stem.split("-")[1]))
        if metric_paths
        else None
    )
    if (
        latest_metric is not None
        and latest_metric.read_text(encoding="utf-8") == rendered_metrics
    ):
        metrics_revision = int(latest_metric.stem.split("-")[1])
    else:
        metrics_revision = (
            max((int(path.stem.split("-")[1]) for path in metric_paths), default=0) + 1
        )
        _write_new(
            target / "metrics" / f"revision-{metrics_revision}.json", rendered_metrics
        )
    latest_value = {
        "schema_version": 3,
        "policy_revision": revision,
        "metrics_revision": metrics_revision,
        "manifest": manifest_name or read_json(latest, {}).get("manifest"),
        "manifest_sha256": previous,
        "event_count": len(records),
    }
    _write_lf(latest, json.dumps(latest_value, indent=2, sort_keys=True) + "\n")
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPOSITORY)
    parser.add_argument(
        "--router-data-root",
        type=Path,
        default=Path(
            os.environ.get("CODEX_ADAPTIVE_ROUTER_DATA", DEFAULT_ROUTER_DATA_ROOT)
        ),
    )
    parser.add_argument("--hook-data-root", type=Path, default=DEFAULT_HOOK_DATA_ROOT)
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    try:
        ensure_clean_clone(args.repo)
        if not args.push:
            with tempfile.TemporaryDirectory() as directory:
                preview = Path(directory) / "repo"
                preview.mkdir()
                routes = write_export(
                    args.router_data_root, args.hook_data_root, preview
                )
            print(
                json.dumps(
                    {
                        "changed": True,
                        "pushed": False,
                        "stored_routes": routes,
                        "worktree_clean": True,
                    }
                )
            )
            return 0
        routes = write_export(args.router_data_root, args.hook_data_root, args.repo)
        run("git", "add", "--", "evolution-data", cwd=args.repo)
        changed = (
            subprocess.run(
                ["git", "diff", "--cached", "--quiet"], cwd=args.repo, check=False
            ).returncode
            != 0
        )
        if not changed:
            print(json.dumps({"changed": False, "stored_routes": routes}))
            return 0
        run(
            "git",
            "commit",
            "-m",
            "chore(evolution): append outcome intelligence batch",
            "--",
            "evolution-data",
            cwd=args.repo,
        )
        run("git", "push", "origin", "HEAD:main", cwd=args.repo)
        print(json.dumps({"changed": True, "pushed": True, "stored_routes": routes}))
        return 0
    except (SyncError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Adaptive Router evolution sync failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
