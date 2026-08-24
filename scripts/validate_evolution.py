"""CI checks for immutable evolution governance, privacy, and recomputable metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import router_core
import sync_evolution_data


def fail(message: str) -> None:
    raise ValueError(message)


def validate(root: Path, base_ref: str | None = None) -> None:
    target = root / "evolution-data"
    legacy = json.loads((target / "legacy-v1.json").read_text(encoding="utf-8"))
    if legacy.get("status") != "legacy-read-only":
        fail("v1 migration marker is missing")
    schema = json.loads(
        (target / "schemas" / "event-v2.schema.json").read_text(encoding="utf-8")
    )
    if schema.get("properties", {}).get("schema_version", {}).get("const") != 2:
        fail("event v2 schema is invalid")
    ids = set()
    previous = None
    events = []
    manifest_paths = (
        list((target / "manifests").glob("manifest-*.json"))
        if (target / "manifests").exists()
        else []
    )
    manifest_paths.sort(key=lambda path: int(path.stem.split("-")[1]))
    for manifest_path in manifest_paths if (target / "manifests").exists() else []:
        manifest = json.loads(manifest_path.read_text())
        batch = target / "batches" / manifest["batch"]
        if manifest.get("schema_version") != 2 or manifest.get("count") != len(
            sync_evolution_data.read_jsonl(batch)
        ):
            fail("manifest schema/count mismatch")
        if manifest.get("sha256") != hashlib.sha256(batch.read_bytes()).hexdigest():
            fail("batch hash mismatch")
        if manifest.get("previous_manifest_sha256") != previous:
            fail("manifest hash chain mismatch")
        previous = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        for event in sync_evolution_data.read_jsonl(batch):
            events.append(event)
            if (
                event.get("schema_version") != 2
                or not event.get("event_id")
                or not isinstance(event.get("sequence"), int)
            ):
                fail("invalid event v2")
            if event["event_id"] in ids:
                fail("duplicate event id")
            ids.add(event["event_id"])
            sync_evolution_data.assert_safe(event)
    metric_paths = (
        list((target / "metrics").glob("revision-*.json"))
        if (target / "metrics").exists()
        else []
    )
    for metrics_path in metric_paths:
        metrics = json.loads(metrics_path.read_text())
        required = {
            "capture_coverage",
            "known_quality_coverage",
            "route_success_wilson_95",
            "brier_score",
        }
        if not required.issubset(metrics):
            fail("metrics artifact is incomplete")
    if events and metric_paths:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            event_path = data_root / "events" / "routing.jsonl"
            event_path.parent.mkdir(parents=True)
            event_path.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in events),
                encoding="utf-8",
            )
            recomputed = router_core.router_metrics(data_root)
        latest_metrics = json.loads(
            max(metric_paths, key=lambda path: int(path.stem.split("-")[1])).read_text(
                encoding="utf-8"
            )
        )
        if recomputed != latest_metrics:
            fail("metrics artifact is not reproducible from immutable events")
    if base_ref:
        changed = subprocess.run(
            ["git", "diff", "--name-status", base_ref, "--", "evolution-data"],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        immutable = re.compile(
            r"^evolution-data/(?:batches|manifests|policies|metrics)/"
        )
        for line in changed:
            status, path = (line.split("\t", 1) + [""])[:2]
            if status != "A" and immutable.match(path.replace("\\", "/")):
                fail(f"immutable artifact modified or deleted: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base-ref")
    args = parser.parse_args()
    try:
        validate(args.root, args.base_ref)
    except (
        ValueError,
        OSError,
        json.JSONDecodeError,
        sync_evolution_data.SyncError,
    ) as error:
        print(f"Evolution validation failed: {error}", file=sys.stderr)
        return 1
    print("Evolution governance validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
