from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER = PLUGIN_ROOT / "scripts" / "router_mcp.py"
WINDOWS_HOOK = PLUGIN_ROOT / "scripts" / "router-hook.cmd"


class RouterMcpTests(unittest.TestCase):
    def test_stdio_server_initializes_and_routes(self) -> None:
        messages = "\n".join(
            [
                json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "route_plan",
                            "arguments": {
                                "task": "Search code for the accounting implementation.",
                                "record": False,
                            },
                        },
                    }
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["CODEX_ADAPTIVE_ROUTER_DATA"] = directory
            result = subprocess.run(
                [sys.executable, str(SERVER)],
                input=messages + "\n",
                text=True,
                capture_output=True,
                cwd=PLUGIN_ROOT,
                env=environment,
                check=True,
            )
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(
            replies[0]["result"]["serverInfo"]["name"], "codex-adaptive-router"
        )
        self.assertEqual(replies[0]["result"]["serverInfo"]["version"], "1.1.0")
        payload = replies[1]["result"]["structuredContent"]
        self.assertEqual(payload["role"], "router_code_mapper")
        self.assertEqual(payload["model"], "gpt-5.6-luna")

    def test_replacement_model_schema_uses_runtime_sol_slug(self) -> None:
        messages = "\n".join(
            [
                json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
                ),
                json.dumps(
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
                ),
            ]
        )
        result = subprocess.run(
            [sys.executable, str(SERVER)],
            input=messages + "\n",
            text=True,
            capture_output=True,
            cwd=PLUGIN_ROOT,
            check=True,
        )
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        tools = {tool["name"]: tool for tool in replies[1]["result"]["tools"]}
        models = tools["record_route_outcome"]["inputSchema"]["properties"][
            "replacement_model"
        ]["enum"]
        self.assertEqual(models, ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
        self.assertIn("task_ref", tools["route_plan"]["inputSchema"]["properties"])
        self.assertIn(
            "decision_features", tools["route_plan"]["inputSchema"]["properties"]
        )
        self.assertIn("router_metrics", tools)

    @unittest.skipUnless(os.name == "nt", "Windows hook wrapper is platform-specific")
    def test_windows_hook_wrapper_returns_preflight_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["CODEX_ADAPTIVE_ROUTER_DATA"] = directory
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", str(WINDOWS_HOOK), "UserPromptSubmit"],
                input=json.dumps(
                    {"prompt": "Search code for the accounting implementation."}
                )
                + "\n",
                text=True,
                capture_output=True,
                cwd=PLUGIN_ROOT,
                env=environment,
                check=True,
            )
        reply = json.loads(result.stdout)
        context = reply["hookSpecificOutput"]["additionalContext"]
        self.assertIn("route=router_code_mapper", context)
        self.assertIn("model=gpt-5.6-luna", context)


if __name__ == "__main__":
    unittest.main()
