from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import router_core


class RouterPlanTests(unittest.TestCase):
    def test_structured_routes(self):
        self.assertEqual(
            router_core.make_route_plan("Search code for references").role,
            "router_code_mapper",
        )
        self.assertEqual(
            router_core.make_route_plan("Run tests and collect metrics").model,
            "gpt-5.6-luna",
        )
        self.assertEqual(
            router_core.make_route_plan(
                "Implement from frozen spec", task_state="frozen"
            ).model,
            "gpt-5.6-terra",
        )
        self.assertEqual(
            router_core.make_route_plan(
                "Audit strategy Sharpe and leakage"
            ).reasoning_effort,
            "xhigh",
        )

    def test_constraints_win_and_max_is_not_automatic(self):
        plan = router_core.make_route_plan(
            "small answer",
            constraints={"model": "gpt-5.6-sol", "reasoning_effort": "max"},
        )
        self.assertEqual(plan.reasoning_effort, "max")
        automatic = router_core.make_route_plan(
            "small answer",
            decision_features={"cognitive_type": "direct", "confidence": 0.9},
            constraints={},
        )
        self.assertNotIn(automatic.reasoning_effort, {"max", "ultra"})


class EngineTests(unittest.TestCase):
    def test_task_correlation_idempotency_and_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            engine = router_core.RouterEngine(Path(d))
            results = []
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        engine.begin_task(
                            session_id="s", turn_id="t", prompt="Search code"
                        )
                    )
                )
                for _ in range(4)
            ]
            [x.start() for x in threads]
            [x.join() for x in threads]
            self.assertEqual(len({x["task_ref"] for x in results}), 1)
            events = router_core._all_events(Path(d))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["sequence"], 1)

    def test_explicit_plan_confirms_hook_route_without_reroute(self):
        with tempfile.TemporaryDirectory() as d:
            engine = router_core.RouterEngine(Path(d))
            task = engine.begin_task(session_id="s", turn_id="t", prompt="Search code")
            route = engine.plan_route(
                "Actually architect this",
                task_ref=task["task_ref"],
                decision_features={"cognitive_type": "architecture"},
            )
            self.assertEqual(route["route_id"], task["route_id"])
            self.assertEqual(route["role"], "router_code_mapper")

    def test_tool_aggregation_dedupe_and_transition(self):
        with tempfile.TemporaryDirectory() as d:
            engine = router_core.RouterEngine(Path(d))
            task = engine.begin_task(session_id="s", turn_id="t", prompt="Run tests")
            payload = {
                "session_id": "s",
                "turn_id": "t",
                "tool_use_id": "x",
                "tool_name": "Agent",
                "tool_input": {
                    "agent_type": "router_experiment_runner",
                    "model": "gpt-5.6-luna",
                    "effort": "medium",
                },
                "tool_response": {},
            }
            engine.observe_event("PostToolUse", payload)
            engine.observe_event("PostToolUse", payload)
            ledger = json.loads(router_core.ledger_path(Path(d)).read_text())
            agg = ledger["tasks"][task["task_ref"]]["aggregate"]
            self.assertEqual(agg["tool_count"], 1)
            self.assertEqual(agg["transitions"][0]["model"], "gpt-5.6-luna")

    def test_late_async_event_stays_with_original_turn(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            engine = router_core.RouterEngine(root)
            first = engine.begin_task(session_id="s", turn_id="1", prompt="Run tests")
            second = engine.begin_task(
                session_id="s", turn_id="2", prompt="Search code"
            )
            engine.observe_event(
                "PostToolUse",
                {
                    "session_id": "s",
                    "turn_id": "1",
                    "tool_use_id": "late",
                    "tool_name": "Bash",
                    "tool_input": {"command": "python -m unittest"},
                    "tool_response": {"exit_code": 0},
                },
            )
            ledger = json.loads(router_core.ledger_path(root).read_text())
            self.assertEqual(
                ledger["tasks"][first["task_ref"]]["aggregate"]["tool_count"], 1
            )
            self.assertEqual(
                ledger["tasks"][second["task_ref"]]["aggregate"]["tool_count"], 0
            )

    def test_crash_recovery_and_session_end_are_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            engine = router_core.RouterEngine(root)
            task = engine.begin_task(session_id="s", turn_id="t", prompt="Search code")
            router_core.ledger_path(root).unlink()
            recovered = router_core.RouterEngine(root)._ledger()
            self.assertIn(task["task_ref"], recovered["tasks"])
            payload = {"session_id": "s", "reason": "other"}
            router_core.record_hook_event("SessionEnd", payload, root)
            router_core.record_hook_event("SessionEnd", payload, root)
            outcomes = [
                x for x in router_core._all_events(root) if x.get("type") == "outcome"
            ]
            self.assertEqual(len(outcomes), 1)

    def test_correction_false_positive_and_explicit_correction(self):
        with tempfile.TemporaryDirectory() as d:
            engine = router_core.RouterEngine(Path(d))
            first = engine.begin_task(
                session_id="s", turn_id="1", prompt="Explain code"
            )
            engine.finalize_task(first["task_ref"])
            engine.begin_task(
                session_id="s", turn_id="2", prompt="Thanks, now explain another file"
            )
            self.assertFalse(
                any(
                    x.get("status") == "corrected"
                    for x in router_core._all_events(Path(d))
                )
            )
            second = engine.begin_task(
                session_id="s", turn_id="3", prompt="Explain code"
            )
            engine.finalize_task(second["task_ref"])
            engine.begin_task(
                session_id="s", turn_id="4", prompt="That was wrong, redo it"
            )
            self.assertTrue(
                any(
                    x.get("status") == "corrected"
                    for x in router_core._all_events(Path(d))
                )
            )

    def test_hmac_privacy_and_v1_policy_read_compatibility(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            task = "private prompt and C:\\secret\\file.py"
            ea = router_core.RouterEngine(Path(a))
            eb = router_core.RouterEngine(Path(b))
            ea.begin_task(session_id="s", turn_id="t", prompt=task, project="C:\\repo")
            eb.begin_task(session_id="s", turn_id="t", prompt=task, project="C:\\repo")
            raw = router_core.events_path(Path(a)).read_text()
            self.assertNotIn(task, raw)
            self.assertNotIn("C:\\repo", raw)
            self.assertNotEqual(
                router_core._all_events(Path(a))[0]["task_ref"],
                router_core._all_events(Path(b))[0]["task_ref"],
            )
            path = router_core.policy_path(Path(a))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"schema_version":1,"revision":7,"overrides":[]}')
            self.assertEqual(router_core.load_policy(Path(a))["revision"], 7)
            self.assertIn(
                "minimum_replacement_outcomes",
                router_core.load_policy(Path(a))["learning"],
            )


class IntelligenceTests(unittest.TestCase):
    def _evidence(self, root: Path, count: int = 5):
        for i in range(count):
            plan = router_core.make_route_plan(
                "Implement from frozen spec", task_state="frozen", root=root
            )
            router_core.create_route_record(
                plan,
                "Implement from frozen spec",
                session_id=f"s{i%3}",
                project_fingerprint="p",
                root=root,
            )
            router_core.record_outcome(
                plan.route_id,
                "escalated",
                confidence=0.9,
                verified=True,
                quality_gate="passed",
                route_fit="under_routed",
                objective_verification=True,
                user_confirmed=True,
                replacement_role="router_researcher",
                replacement_model="gpt-5.6-sol",
                replacement_effort="high",
                root=root,
            )

    def test_objective_gate_axis_proposals_metrics_and_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._evidence(root)
            proposals = router_core.learning_proposals(root)
            self.assertEqual({x["axis"] for x in proposals}, {"role", "model"})
            self.assertTrue(all(x["status"] == "ready_for_shadow" for x in proposals))
            metrics = router_core.router_metrics(root)
            self.assertEqual(metrics["route_success"], 1.0)
            self.assertIsNotNone(metrics["route_success_wilson_95"]["low"])
            proposal = next(x for x in proposals if x["axis"] == "model")
            router_core.start_shadow(proposal["proposal_id"], root)
            for _ in range(10):
                router_core.record_shadow_observation(
                    proposal["proposal_id"], True, root
                )
            with self.assertRaises(ValueError):
                router_core.confirm_policy_change(proposal["proposal_id"], False, root)
            override = router_core.confirm_policy_change(
                proposal["proposal_id"], True, root
            )
            self.assertEqual(override["axis"], "model")

    def test_verified_high_risk_regression_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for i in range(5):
                plan = router_core.make_route_plan(
                    "Implement from frozen spec", task_state="frozen", root=root
                )
                router_core.create_route_record(
                    plan, "task", session_id=f"s{i%3}", root=root
                )
                router_core.record_outcome(
                    plan.route_id,
                    "escalated",
                    confidence=0.9,
                    verified=True,
                    quality_gate="failed",
                    route_fit="under_routed",
                    objective_verification=True,
                    user_confirmed=True,
                    high_risk_regression=i == 0,
                    replacement_role="router_researcher",
                    replacement_model="gpt-5.6-sol",
                    replacement_effort="high",
                    root=root,
                )
            self.assertTrue(
                all(
                    x["status"] == "collecting_evidence" and x["high_risk_regression"]
                    for x in router_core.learning_proposals(root)
                )
            )

    def test_no_proposal_without_objective_and_user_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            plan = router_core.make_route_plan(
                "Implement from frozen spec", task_state="frozen", root=root
            )
            router_core.create_route_record(plan, "task", root=root)
            router_core.record_outcome(
                plan.route_id,
                "escalated",
                confidence=0.99,
                replacement_role="router_researcher",
                replacement_model="gpt-5.6-sol",
                replacement_effort="high",
                root=root,
            )
            self.assertEqual(router_core.learning_proposals(root), [])

    def test_unexecuted_shadow_downgrade_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for i in range(5):
                plan = router_core.make_route_plan("small direct answer", root=root)
                router_core.create_route_record(
                    plan,
                    "small direct answer",
                    session_id=f"s{i%3}",
                    project_fingerprint=f"p{i%2}",
                    root=root,
                )
                router_core.record_outcome(
                    plan.route_id,
                    "overridden",
                    confidence=0.9,
                    verified=True,
                    quality_gate="passed",
                    route_fit="over_routed",
                    objective_verification=True,
                    user_confirmed=True,
                    replacement_role="direct",
                    replacement_model="gpt-5.6-luna",
                    replacement_effort="medium",
                    root=root,
                )
            proposal = next(
                x for x in router_core.learning_proposals(root) if x["axis"] == "model"
            )
            router_core.start_shadow(proposal["proposal_id"], root)
            engine = router_core.RouterEngine(root)
            task = engine.begin_task(
                session_id="new",
                turn_id="new",
                prompt="small direct answer",
                project="p3",
            )
            engine.finalize_task(
                task["task_ref"],
                quality_gate="passed",
                route_fit="adequate",
                verified=True,
                objective_verification=True,
            )
            item = router_core.load_shadows(root)["items"][proposal["proposal_id"]]
            self.assertEqual(item["observations"][-1]["result"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
