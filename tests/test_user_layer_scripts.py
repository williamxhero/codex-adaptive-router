from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN_ROOT / "scripts" / "install_user_layer.py"
UNINSTALLER = PLUGIN_ROOT / "scripts" / "uninstall_user_layer.py"


class UserLayerScriptsTests(unittest.TestCase):
    def test_install_is_scoped_and_uninstall_removes_only_router_agents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex-home"
            agents = codex_home / "agents"
            agents.mkdir(parents=True)
            (agents / "other_agent.toml").write_text('model = "gpt-5.6"\n', encoding="utf-8")
            (codex_home / "config.toml").write_text("approval_policy = \"never\"\n", encoding="utf-8")

            subprocess.run(
                [sys.executable, str(INSTALLER), "--codex-home", str(codex_home), "--set-root-model"],
                text=True,
                capture_output=True,
                check=True,
            )
            config = (codex_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model = "gpt-5.6"', config)
            self.assertIn('model_reasoning_effort = "medium"', config)
            self.assertIn("[agents]", config)
            self.assertEqual(len(list(agents.glob("router_*.toml"))), 7)
            self.assertTrue((agents / "other_agent.toml").exists())

            subprocess.run(
                [sys.executable, str(UNINSTALLER), "--codex-home", str(codex_home), "--apply"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertFalse(list(agents.glob("router_*.toml")))
            self.assertTrue((agents / "other_agent.toml").exists())


if __name__ == "__main__":
    unittest.main()
