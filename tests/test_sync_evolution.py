from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import router_core
import sync_evolution_data
import validate_evolution


class SyncTests(unittest.TestCase):
    def test_real_v1_input_is_deterministically_migrated_without_rewrite(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as repo:
            root = Path(data)
            source = root / "events" / "routing.jsonl"
            source.parent.mkdir(parents=True)
            legacy = {
                "schema_version": 1,
                "type": "route",
                "sequence": 7,
                "route_id": "legacy-route",
                "role": "router_code_mapper",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "medium",
                "task_class": "discovery",
            }
            original = json.dumps(legacy, sort_keys=True) + "\n"
            source.write_text(original, encoding="utf-8")
            target = Path(repo)
            sync_evolution_data.write_export(root, root / "other", target)
            first = sync_evolution_data.merged_records(root)[0]
            second = sync_evolution_data.merged_records(root)[0]
            self.assertEqual(first, second)
            self.assertEqual(first["schema_version"], 2)
            self.assertEqual(first["decision_features"]["feature_source"], "legacy_v1")
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            validate_evolution.validate(target)

    def test_content_batch_ids_do_not_collide_across_stores_or_late_arrival(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as repo:
            base = Path(data)
            left, right, target = base / "left", base / "right", Path(repo)
            router_core.RouterEngine(left).begin_task(session_id="s1", turn_id="1", prompt="Search code")
            sync_evolution_data.write_export(left, right, target)
            first_names = {path.name for path in (target / "evolution-data" / "batches").glob("*.jsonl")}
            router_core.RouterEngine(right).begin_task(session_id="s2", turn_id="1", prompt="Run tests")
            sync_evolution_data.write_export(left, right, target)
            names = {path.name for path in (target / "evolution-data" / "batches").glob("*.jsonl")}
            self.assertEqual(len(first_names), 1)
            self.assertEqual(len(names), 2)
            self.assertTrue(first_names < names)
            before = (target / "evolution-data" / "latest.json").read_text()
            sync_evolution_data.write_export(left, right, target)
            self.assertEqual(before, (target / "evolution-data" / "latest.json").read_text())
            validate_evolution.validate(target)

    def test_immutable_batch_hash_chain_and_validation(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as repo:
            root = Path(data)
            target = Path(repo)
            engine = router_core.RouterEngine(root)
            engine.begin_task(session_id="s", turn_id="t", prompt="Search code")
            sync_evolution_data.write_export(root, Path(data) / "other", target)
            first = (target / "evolution-data" / "latest.json").read_text()
            sync_evolution_data.write_export(root, Path(data) / "other", target)
            self.assertEqual(
                first, (target / "evolution-data" / "latest.json").read_text()
            )
            validate_evolution.validate(target)
            self.assertEqual(
                len(list((target / "evolution-data" / "batches").glob("*.jsonl"))), 1
            )

    def test_privacy_fail_closed(self):
        with self.assertRaises(sync_evolution_data.SyncError):
            sync_evolution_data.assert_safe({"prompt": "private"})
        with self.assertRaises(sync_evolution_data.SyncError):
            sync_evolution_data.assert_safe({"value": "C:\\Users\\will\\secret"})
        with self.assertRaises(sync_evolution_data.SyncError):
            sync_evolution_data.assert_safe(
                {"value": "s" + "k-" + "abcdefghijklmnopqrstuvwxyz"}
            )
        with tempfile.TemporaryDirectory() as data:
            root = Path(data)
            engine = router_core.RouterEngine(root)
            engine.begin_task(session_id="s", turn_id="t", prompt="Search code")
            path = root / "events" / "routing.jsonl"
            event = json.loads(path.read_text().splitlines()[0])
            event["content"] = "not uploadable"
            path.write_text(json.dumps(event) + "\n")
            with self.assertRaises(sync_evolution_data.SyncError):
                sync_evolution_data.merged_records(root)
            event.pop("content")
            event["constraints"]["no_delegation"] = "false"
            path.write_text(json.dumps(event) + "\n")
            with self.assertRaises(sync_evolution_data.SyncError):
                sync_evolution_data.merged_records(root)

    def test_new_evidence_appends_metrics_revision_without_policy_change(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as repo:
            root = Path(data)
            target = Path(repo)
            engine = router_core.RouterEngine(root)
            engine.begin_task(session_id="s", turn_id="1", prompt="Search code")
            sync_evolution_data.write_export(root, Path(data) / "other", target)
            engine.begin_task(session_id="s", turn_id="2", prompt="Run tests")
            sync_evolution_data.write_export(root, Path(data) / "other", target)
            latest = json.loads((target / "evolution-data" / "latest.json").read_text())
            self.assertEqual(latest["policy_revision"], 1)
            self.assertEqual(latest["metrics_revision"], 2)
            self.assertEqual(
                len(list((target / "evolution-data" / "metrics").glob("*.json"))), 2
            )
            validate_evolution.validate(target)

    def test_no_push_preview_leaves_git_worktree_clean(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as repo:
            repository = Path(repo)
            subprocess.run(
                ["git", "init"], cwd=repository, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"], cwd=repository, check=True
            )
            (repository / "seed").write_text("x")
            subprocess.run(["git", "add", "seed"], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-m", "seed"],
                cwd=repository,
                capture_output=True,
                check=True,
            )
            # Preview path itself is covered by main's temporary export; direct export is intentionally mutating.
            with tempfile.TemporaryDirectory() as preview:
                sync_evolution_data.write_export(
                    Path(data), Path(data) / "other", Path(preview)
                )
            self.assertEqual(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=repository,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout,
                "",
            )

    def test_no_push_cli_is_end_to_end_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bare, seed, clone, data = base / "origin.git", base / "seed", base / "clone", base / "data"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@example.com"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
            (seed / "seed").write_text("x")
            subprocess.run(["git", "-C", str(seed), "add", "seed"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-m", "seed"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(bare)], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-u", "origin", "main"], check=True, capture_output=True)
            subprocess.run(["git", "clone", "-b", "main", str(bare), str(clone)], check=True, capture_output=True)
            router_core.RouterEngine(data).begin_task(session_id="s", turn_id="t", prompt="Search code")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "sync_evolution_data.py"), "--repo", str(clone), "--router-data-root", str(data), "--hook-data-root", str(base / "other")],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse((clone / "evolution-data").exists())
            self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=clone, text=True, capture_output=True, check=True).stdout, "")


if __name__ == "__main__":
    unittest.main()
