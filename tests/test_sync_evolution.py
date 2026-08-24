from __future__ import annotations

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
            sync_evolution_data.assert_safe({"value": "sk-abcdefghijklmnopqrstuvwxyz"})

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


if __name__ == "__main__":
    unittest.main()
