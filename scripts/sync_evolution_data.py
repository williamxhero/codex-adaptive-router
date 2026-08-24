"""Export privacy-bounded Adaptive Router evidence and push it to GitHub.

The export deliberately excludes raw prompts, paths, tool output, code, logs,
credentials, and secrets.  It uses a dedicated clean clone so an unrelated
plugin development worktree can never be committed by this job.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPOSITORY_URL = "https://github.com/williamxhero/codex-adaptive-router.git"
DEFAULT_REPOSITORY = Path.home() / ".codex" / "state" / "codex-adaptive-router-evolution-repo"
DEFAULT_ROUTER_DATA_ROOT = Path.home() / ".codex" / "codex-adaptive-router"
DEFAULT_HOOK_DATA_ROOT = Path.home() / ".codex" / "plugins" / "data" / "codex-adaptive-router-personal"
FORBIDDEN_KEYS = {
    "task", "prompt", "raw_prompt", "path", "source_path", "output", "tool_output",
    "code", "log", "logs", "credential", "credentials", "secret", "secrets", "token",
}


class SyncError(RuntimeError):
    pass


def run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if completed.returncode:
        raise SyncError(f"{' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout.strip()


def assert_safe(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise SyncError(f"refusing to export forbidden field: {key}")
            assert_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_safe(item)


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    value = json.loads(path.read_text(encoding="utf-8"))
    assert_safe(value)
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise SyncError(f"invalid routing event at line {line_number}: {error}") from error
        if not isinstance(record, dict):
            raise SyncError(f"routing event at line {line_number} is not an object")
        assert_safe(record)
        records.append(record)
    return records


def ensure_clean_clone(repository: Path) -> None:
    if not repository.exists():
        repository.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", REPOSITORY_URL, str(repository))
    if not (repository / ".git").is_dir():
        raise SyncError(f"repository path is not a git clone: {repository}")
    if run("git", "status", "--porcelain", cwd=repository):
        raise SyncError("dedicated evolution clone is dirty; refusing to touch it")
    run("git", "fetch", "origin", "main", cwd=repository)
    run("git", "checkout", "main", cwd=repository)
    run("git", "pull", "--ff-only", "origin", "main", cwd=repository)


def merged_records(*roots: Path) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for root in roots:
        for record in read_jsonl(root / "events" / "routing.jsonl"):
            unique[json.dumps(record, ensure_ascii=False, sort_keys=True)] = record
    return [unique[key] for key in sorted(unique)]


def write_export(router_data_root: Path, hook_data_root: Path, repository: Path) -> int:
    records = merged_records(router_data_root, hook_data_root)
    policy = read_json(router_data_root / "policy" / "current.json", {})
    shadows = read_json(router_data_root / "learning" / "shadows.json", {})
    route_count = sum(record.get("type") == "route" for record in records)
    outcome_count = sum(record.get("type") == "outcome" for record in records)
    exported_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    target = repository / "evolution-data"
    target.mkdir(parents=True, exist_ok=True)
    (target / "routing.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    (target / "policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "shadows.json").write_text(json.dumps(shadows, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "status.json").write_text(json.dumps({
        "schema_version": 1,
        "exported_at": exported_at,
        "stored_routes": route_count,
        "stored_outcomes": outcome_count,
        "policy_revision": policy.get("revision", policy.get("policy_revision", 1)),
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "README.md").write_text(
        "# Adaptive Router evolution data\n\n"
        "This directory is generated by the user-level sync job. It contains only privacy-bounded "
        "routing metadata: hashed identifiers, route/outcome evidence, policy revisions, and shadow "
        "evaluation state. Raw prompts, paths, source, tool output, logs, credentials, and secrets are rejected.\n",
        encoding="utf-8",
    )
    return route_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPOSITORY)
    parser.add_argument("--router-data-root", type=Path, default=Path(os.environ.get("CODEX_ADAPTIVE_ROUTER_DATA", DEFAULT_ROUTER_DATA_ROOT)))
    parser.add_argument("--hook-data-root", type=Path, default=DEFAULT_HOOK_DATA_ROOT)
    parser.add_argument("--push", action="store_true", help="commit and push an export when evidence changed")
    args = parser.parse_args()

    try:
        ensure_clean_clone(args.repo)
        routes = write_export(args.router_data_root, args.hook_data_root, args.repo)
        run("git", "add", "--", "evolution-data", cwd=args.repo)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=args.repo).returncode != 0
        if not changed:
            print(json.dumps({"changed": False, "stored_routes": routes}))
            return 0
        if not args.push:
            run("git", "restore", "--staged", "--", "evolution-data", cwd=args.repo)
            print(json.dumps({"changed": True, "pushed": False, "stored_routes": routes}))
            return 0
        run("git", "commit", "-m", "chore(evolution): sync adaptive router evidence", "--", "evolution-data", cwd=args.repo)
        run("git", "push", "origin", "HEAD:main", cwd=args.repo)
        print(json.dumps({"changed": True, "pushed": True, "stored_routes": routes}))
        return 0
    except SyncError as error:
        print(f"Adaptive Router evolution sync failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
