from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER = PLUGIN_ROOT / "scripts" / "router_mcp.py"
WINDOWS_HOOK = PLUGIN_ROOT / "scripts" / "router-hook.cmd"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import router_mcp


class RouterMcpTests(unittest.TestCase):
    def test_required_evidence_hooks_are_synchronous_for_current_codex(self) -> None:
        configuration = json.loads(
            (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        for event in ("PostToolUse", "SubagentStop", "Stop"):
            hooks = [
                hook
                for registration in configuration["hooks"][event]
                for hook in registration["hooks"]
            ]
            self.assertTrue(hooks, event)
            for hook in hooks:
                self.assertEqual(hook["timeout"], 3, event)
                self.assertNotIn("async", hook, event)

    def test_outcome_schema_and_call_support_exceptional_audit_followup(self) -> None:
        reply = router_mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        tools = {tool["name"]: tool for tool in reply["result"]["tools"]}
        signal = tools["record_route_outcome"]["inputSchema"]["properties"][
            "result_signal"
        ]
        outcome_schema = tools["record_route_outcome"]["inputSchema"]["properties"]
        self.assertEqual(
            set(signal["enum"]),
            {"normal", "exceptional_positive", "exceptional_negative", "unknown"},
        )
        self.assertIn("raw metrics", signal["description"])
        self.assertIn("lifecycle", outcome_schema["stage"]["description"])
        self.assertIn(
            "stop alone is never verification",
            outcome_schema["objective_verification"]["description"],
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"CODEX_ADAPTIVE_ROUTER_DATA": directory}
        ):
            route = router_mcp._tool_call(
                "route_plan",
                {
                    "task": "Run the defined backtest",
                    "decision_features": {"cognitive_type": "execution"},
                },
            )
            outcome = router_mcp._tool_call(
                "record_route_outcome",
                {
                    "route_id": route["route_id"],
                    "status": "verified",
                    "confidence": 0.95,
                    "verified": True,
                    "quality_gate": "passed",
                    "objective_verification": True,
                    "result_signal": "exceptional_positive",
                },
            )
            self.assertEqual(
                outcome["audit_followup"]["role"],
                "router_adversarial_auditor",
            )

    def test_release_versions_are_consistent(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        initialized = router_mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(manifest["version"], "1.2.0")
        self.assertEqual(
            initialized["result"]["serverInfo"]["version"], manifest["version"]
        )

    def test_hook_and_independent_mcp_share_task_ref_without_plugin_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex-home"
            legacy_root = codex_home / "plugins" / "data" / "codex-adaptive-router-test"
            hook_environment = os.environ.copy()
            hook_environment.pop("CODEX_ADAPTIVE_ROUTER_DATA", None)
            hook_environment["CODEX_HOME"] = str(codex_home)
            hook_environment["PLUGIN_DATA"] = str(legacy_root)
            hook = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "scripts" / "router_hook.py"), "UserPromptSubmit"],
                input=json.dumps(
                    {
                        "session_id": "session-1",
                        "turn_id": "turn-1",
                        "prompt": "Search code for the accounting implementation.",
                    }
                )
                + "\n",
                text=True,
                capture_output=True,
                cwd=PLUGIN_ROOT,
                env=hook_environment,
                check=True,
            )
            context = json.loads(hook.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            task_ref = context.split("task_ref=", 1)[1].split(";", 1)[0]

            mcp_environment = hook_environment.copy()
            mcp_environment.pop("PLUGIN_DATA")
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "route_plan",
                    "arguments": {"task_ref": task_ref},
                },
            }
            mcp = subprocess.run(
                [sys.executable, str(SERVER)],
                input=json.dumps(request) + "\n",
                text=True,
                capture_output=True,
                cwd=PLUGIN_ROOT,
                env=mcp_environment,
                check=True,
            )
            reply = json.loads(mcp.stdout)
            route = reply["result"]["structuredContent"]

            route_events = []
            for root in (codex_home / "codex-adaptive-router", legacy_root):
                path = root / "events" / "routing.jsonl"
                if path.exists():
                    route_events.extend(
                        item
                        for item in map(json.loads, path.read_text().splitlines())
                        if item["type"] == "route"
                    )
            self.assertEqual(len(route_events), 1)
            self.assertEqual(route["route_id"], route_events[0]["route_id"])

    def test_task_ref_imports_matching_legacy_store_once_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex-home"
            legacy_root = (
                codex_home / "plugins" / "data" / "codex-adaptive-router-market"
            )
            legacy_engine = router_mcp.router_core.RouterEngine(legacy_root)
            task = legacy_engine.begin_task(
                session_id="legacy-session",
                turn_id="legacy-turn",
                prompt="Search code for the accounting implementation.",
            )
            legacy_engine.finalize_task(
                task["task_ref"],
                status="completed",
                quality_gate="provisional",
                confidence=0.5,
            )
            canonical_root = codex_home / "codex-adaptive-router"
            router_mcp.router_core.RouterEngine(canonical_root).begin_task(
                session_id="canonical-session",
                turn_id="canonical-turn",
                prompt="Run tests and collect metrics.",
            )
            legacy_events = router_mcp.router_core.events_path(legacy_root)
            legacy_ledger = router_mcp.router_core.ledger_path(legacy_root)
            original_events = legacy_events.read_bytes()
            original_ledger = legacy_ledger.read_bytes()

            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(codex_home),
                    "CODEX_ADAPTIVE_ROUTER_DATA": "",
                    "PLUGIN_DATA": "",
                },
            ):
                first = router_mcp._tool_call(
                    "route_plan", {"task_ref": task["task_ref"]}
                )
                second = router_mcp._tool_call(
                    "route_plan", {"task_ref": task["task_ref"]}
                )

            self.assertEqual(first["route_id"], task["route_id"])
            self.assertEqual(second, first)
            self.assertEqual(legacy_events.read_bytes(), original_events)
            self.assertEqual(legacy_ledger.read_bytes(), original_ledger)
            imported = router_mcp.router_core._all_events(canonical_root)
            matching = [
                item for item in imported if item["task_ref"] == task["task_ref"]
            ]
            self.assertEqual(len(imported), 3)
            self.assertEqual(len(matching), 2)
            self.assertEqual(matching[0]["route_id"], task["route_id"])
            self.assertEqual([item["sequence"] for item in imported], [1, 2, 3])

    def test_task_ref_fails_closed_on_conflicting_legacy_route_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex-home"
            first_root = codex_home / "plugins" / "data" / "codex-adaptive-router-a"
            second_root = codex_home / "plugins" / "data" / "codex-adaptive-router-b"
            task = router_mcp.router_core.RouterEngine(first_root).begin_task(
                session_id="legacy-session",
                turn_id="legacy-turn",
                prompt="Search code for the accounting implementation.",
            )
            event = router_mcp.router_core._all_events(first_root)[0]
            conflicting = dict(event)
            conflicting["route_id"] = "f" * 32
            conflicting["event_id"] = "e" * 32
            conflicting["dedupe_key"] = f"route:{task['task_ref']}:conflict"
            target = router_mcp.router_core.events_path(second_root)
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(conflicting) + "\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(codex_home),
                    "CODEX_ADAPTIVE_ROUTER_DATA": "",
                    "PLUGIN_DATA": "",
                },
            ), self.assertRaisesRegex(ValueError, "conflicting legacy route_id"):
                router_mcp._tool_call(
                    "route_plan", {"task_ref": task["task_ref"]}
                )

            canonical_root = codex_home / "codex-adaptive-router"
            self.assertFalse(router_mcp.router_core.events_path(canonical_root).exists())

    def test_legacy_import_preserves_partial_canonical_log_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex-home"
            legacy_root = (
                codex_home / "plugins" / "data" / "codex-adaptive-router-market"
            )
            task = router_mcp.router_core.RouterEngine(legacy_root).begin_task(
                session_id="legacy-session",
                turn_id="legacy-turn",
                prompt="Search code for the accounting implementation.",
            )
            canonical_events = router_mcp.router_core.events_path(
                codex_home / "codex-adaptive-router"
            )
            canonical_events.parent.mkdir(parents=True)
            partial = b'{"partial":'
            canonical_events.write_bytes(partial)

            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(codex_home),
                    "CODEX_ADAPTIVE_ROUTER_DATA": "",
                    "PLUGIN_DATA": "",
                },
            ), self.assertRaisesRegex(ValueError, "invalid canonical event log"):
                router_mcp._tool_call(
                    "route_plan", {"task_ref": task["task_ref"]}
                )

            self.assertEqual(canonical_events.read_bytes(), partial)

    def test_legacy_import_rejects_unsafe_canonical_event_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex-home"
            legacy_root = (
                codex_home / "plugins" / "data" / "codex-adaptive-router-market"
            )
            task = router_mcp.router_core.RouterEngine(legacy_root).begin_task(
                session_id="legacy-session",
                turn_id="legacy-turn",
                prompt="Search code for the accounting implementation.",
            )
            canonical_root = codex_home / "codex-adaptive-router"
            router_mcp.router_core.RouterEngine(canonical_root).begin_task(
                session_id="canonical-session",
                turn_id="canonical-turn",
                prompt="Run tests and collect metrics.",
            )
            canonical_events = router_mcp.router_core.events_path(canonical_root)
            unsafe = router_mcp.router_core._all_events(canonical_root)
            unsafe[0]["raw_prompt"] = "must never migrate"
            canonical_events.write_text(
                json.dumps(unsafe[0]) + "\n", encoding="utf-8"
            )
            original = canonical_events.read_bytes()

            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(codex_home),
                    "CODEX_ADAPTIVE_ROUTER_DATA": "",
                    "PLUGIN_DATA": "",
                },
            ), self.assertRaisesRegex(ValueError, "invalid fields"):
                router_mcp._tool_call(
                    "route_plan", {"task_ref": task["task_ref"]}
                )

            self.assertEqual(canonical_events.read_bytes(), original)

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
        self.assertEqual(replies[0]["result"]["serverInfo"]["version"], "1.2.0")
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

    def test_route_plan_schema_matches_decision_features_v2(self) -> None:
        reply = router_mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        tools = {tool["name"]: tool for tool in reply["result"]["tools"]}
        route_plan = tools["route_plan"]
        schema = route_plan["inputSchema"]
        features = schema["properties"]["decision_features"]
        expected_fields = {
            "operation_mode",
            "scope",
            "spec_state",
            "reversibility",
            "cognitive_type",
            "risk_domains",
            "workload",
            "user_constraints",
            "feature_source",
            "confidence",
            "feature_version",
            "verification_depth",
            "evidence_state",
            "decision_impact",
            "novelty",
        }
        self.assertEqual(set(features["properties"]), expected_fields)
        self.assertNotIn("required", features)
        self.assertFalse(features["additionalProperties"])
        expected_enums = {
            "operation_mode": {
                "answer",
                "diagnose",
                "change",
                "review",
                "research",
                "execute",
                "monitor",
            },
            "scope": {"tiny", "bounded", "multi_file", "cross_system"},
            "spec_state": {"unknown", "ambiguous", "frozen"},
            "reversibility": {"reversible", "costly", "irreversible"},
            "cognitive_type": {
                "direct",
                "discovery",
                "execution",
                "implementation",
                "diagnosis",
                "research",
                "architecture",
                "audit",
                "exploration",
            },
            "workload": {"small", "medium", "large", "batch"},
            "feature_source": {
                "structured_heuristic",
                "caller_supplied",
                "legacy_v1",
            },
            "verification_depth": {"basic", "standard", "deep", "adversarial"},
            "evidence_state": {"unknown", "consistent", "conflicting"},
            "decision_impact": {"low", "medium", "high", "critical"},
            "novelty": {"routine", "novel", "open_ended"},
        }
        for field, expected in expected_enums.items():
            self.assertEqual(set(features["properties"][field]["enum"]), expected)
        self.assertEqual(
            set(features["properties"]["risk_domains"]["items"]["enum"]),
            {
                "quantitative_research",
                "high_impact",
                "security",
                "privacy",
                "production",
                "financial",
                "legal",
                "medical",
            },
        )
        self.assertEqual(
            set(features["properties"]["user_constraints"]["items"]["enum"]),
            {"role", "model", "reasoning_effort", "no_delegation"},
        )
        self.assertEqual(
            features["properties"]["confidence"],
            {"type": "number", "minimum": 0, "maximum": 1},
        )
        self.assertEqual(features["properties"]["feature_version"], {"type": "integer", "const": 2})
        constraints = schema["properties"]["constraints"]
        self.assertEqual(
            set(constraints["properties"]),
            {"role", "model", "reasoning_effort", "no_delegation"},
        )
        self.assertFalse(constraints["additionalProperties"])
        task_state = schema["properties"]["task_state"]
        self.assertEqual(task_state["enum"], ["unknown", "frozen"])
        self.assertIn("only", task_state["description"].casefold())
        self.assertIn("Decision Features v2", route_plan["description"])
        self.assertIn("capability floor", route_plan["description"].casefold())

    def test_route_plan_accepts_only_known_task_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"CODEX_ADAPTIVE_ROUTER_DATA": directory}
        ):
            first = router_mcp._tool_call(
                "route_plan", {"task": "Search code", "session_id": "s"}
            )
            confirmed = router_mcp._tool_call(
                "route_plan", {"task_ref": first["task_ref"]}
            )
            self.assertEqual(confirmed["route_id"], first["route_id"])
            with self.assertRaisesRegex(ValueError, "unknown task_ref"):
                router_mcp._tool_call("route_plan", {"task_ref": "a" * 32})

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
