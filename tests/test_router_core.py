from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import router_core


class RouterPlanTests(unittest.TestCase):
    def test_profile_v3_separates_authority_capability_and_effort(self):
        model_order = {
            "gpt-5.6-luna": 1,
            "gpt-5.6-terra": 2,
            "gpt-5.6-sol": 3,
        }
        authority_floor = {
            "evidence": "gpt-5.6-luna",
            "implementation": "gpt-5.6-terra",
            "decision": "gpt-5.6-sol",
            "audit": "gpt-5.6-sol",
        }
        for name in ("generic", "quant"):
            profile = router_core.load_profile(name)
            self.assertEqual(profile["schema_version"], 3)
            for config in profile["roles"].values():
                floor = config["capability_floor"]
                self.assertEqual(floor, authority_floor[config["authority"]])
                self.assertIn(config["default_model"], config["allowed_models"])
                self.assertTrue(
                    all(
                        model_order[model] >= model_order[floor]
                        for model in config["allowed_models"]
                    )
                )
                self.assertEqual(
                    set(config["effort"]), {"min", "default", "max"}
                )
                self.assertIn("sol_escalation_conditions", config)

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
        with self.assertRaises(ValueError):
            router_core.make_route_plan(
                "small answer", constraints={"no_delegation": "false"}
            )

    def test_role_policy_override_reloads_legal_role_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            policy = router_core.default_policy()
            policy["overrides"] = [
                {
                    "profile": "generic",
                    "task_class": "discovery",
                    "axis": "role",
                    "to": "router_researcher",
                }
            ]
            router_core.save_policy(policy, root)
            plan = router_core.make_route_plan("Search code for references", root=root)
            self.assertEqual(
                (plan.role, plan.model, plan.reasoning_effort),
                ("router_researcher", "gpt-5.6-sol", "medium"),
            )

    def test_decision_features_v2_accept_partial_and_fill_deterministically(self):
        features = router_core.infer_decision_features(
            "Investigate the failure",
            supplied={
                "verification_depth": "deep",
                "evidence_state": "conflicting",
            },
        )
        self.assertEqual(features["feature_version"], 2)
        self.assertEqual(features["feature_source"], "caller_supplied")
        self.assertEqual(features["verification_depth"], "deep")
        self.assertEqual(features["evidence_state"], "conflicting")
        self.assertIn(features["decision_impact"], {"low", "medium", "high", "critical"})
        self.assertIn(features["novelty"], {"routine", "novel", "open_ended"})
        with self.assertRaisesRegex(ValueError, "unknown decision feature"):
            router_core.infer_decision_features("task", supplied={"task_kind": "bug"})
        with self.assertRaisesRegex(ValueError, "invalid decision feature"):
            router_core.infer_decision_features(
                "task", supplied={"verification_depth": "exhaustive"}
            )

    def test_capability_floor_precedes_effort_and_decision_stays_sol_owned(self):
        research = router_core.make_route_plan(
            "Research an architecture decision",
            decision_features={
                "cognitive_type": "research",
                "verification_depth": "deep",
            },
            constraints={"model": "gpt-5.6-terra", "reasoning_effort": "xhigh"},
        )
        self.assertEqual(research.capability_floor, "gpt-5.6-sol")
        self.assertEqual(research.model, "gpt-5.6-sol")
        decision_stages = [
            stage for stage in research.stages if stage["authority"] == "decision"
        ]
        self.assertTrue(decision_stages)
        self.assertEqual(decision_stages[-1]["model"], "gpt-5.6-sol")
        self.assertEqual(research.capability_exception["requested_model"], "gpt-5.6-terra")
        self.assertEqual(research.capability_exception["disposition"], "worker_only")
        self.assertTrue(
            all(
                stage["model"] == "gpt-5.6-sol"
                for stage in research.stages
                if stage["authority"] in {"decision", "audit"}
            )
        )

    def test_route_plan_v2_templates_and_effort_precedence(self):
        tiny = router_core.make_route_plan(
            "Rename x",
            decision_features={
                "cognitive_type": "direct",
                "scope": "tiny",
                "verification_depth": "basic",
            },
        )
        self.assertEqual((tiny.plan_version, tiny.route_mode), (2, "single"))
        self.assertEqual(len(tiny.stages), 1)
        self.assertEqual(
            (tiny.stages[0]["role"], tiny.stages[0]["model"], tiny.stages[0]["reasoning_effort"]),
            ("direct", "gpt-5.6-sol", "medium"),
        )

        discovery = router_core.make_route_plan(
            "Search code for references",
            decision_features={"cognitive_type": "discovery"},
        )
        self.assertEqual(discovery.route_mode, "staged")
        self.assertEqual(
            [(stage["stage"], stage["model"]) for stage in discovery.stages],
            [("collect", "gpt-5.6-luna"), ("synthesize", "gpt-5.6-sol")],
        )

        implementation = router_core.make_route_plan(
            "Implement the frozen multi-file change",
            task_state="frozen",
            decision_features={
                "cognitive_type": "implementation",
                "scope": "multi_file",
                "spec_state": "frozen",
            },
        )
        self.assertEqual(
            [(stage["stage"], stage["model"]) for stage in implementation.stages],
            [
                ("frame", "gpt-5.6-sol"),
                ("implement", "gpt-5.6-terra"),
                ("verify", "gpt-5.6-luna"),
                ("synthesize", "gpt-5.6-sol"),
            ],
        )
        self.assertEqual(implementation.reasoning_effort, "high")
        self.assertIn("broad_scope", implementation.effort_basis)

        audit = router_core.make_route_plan(
            "Review an exceptional strategy result",
            profile="quant",
            decision_features={
                "cognitive_type": "research",
                "decision_impact": "high",
                "evidence_state": "consistent",
                "novelty": "novel",
            },
        )
        self.assertEqual(audit.stages[-1]["stage"], "audit")
        self.assertEqual(audit.stages[-1]["reasoning_effort"], "xhigh")

        quant_attribution = router_core.make_route_plan(
            "Implement a frozen quant attribution study",
            profile="quant",
            task_state="frozen",
            decision_features={
                "operation_mode": "change",
                "cognitive_type": "research",
                "spec_state": "frozen",
            },
        )
        self.assertEqual(
            [stage["model"] for stage in quant_attribution.stages],
            [
                "gpt-5.6-sol",
                "gpt-5.6-luna",
                "gpt-5.6-terra",
                "gpt-5.6-sol",
            ],
        )

    def test_automatic_effort_never_uses_max_or_ultra(self):
        cases = [
            {"verification_depth": "adversarial"},
            {"decision_impact": "critical"},
            {"novelty": "open_ended"},
            {"reversibility": "irreversible"},
        ]
        for supplied in cases:
            plan = router_core.make_route_plan(
                "Complex review", decision_features=supplied
            )
            self.assertNotIn(plan.reasoning_effort, {"max", "ultra"})
            self.assertTrue(
                all(
                    stage["reasoning_effort"] not in {"max", "ultra"}
                    for stage in plan.stages
                )
            )


class EngineTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Win32 process query is Windows-specific")
    def test_windows_process_alive_never_uses_os_kill(self):
        with mock.patch.object(
            router_core.os,
            "kill",
            side_effect=AssertionError("Windows process query called os.kill"),
        ):
            self.assertTrue(router_core._process_alive(os.getpid()))
            self.assertFalse(router_core._process_alive(0x7FFFFFFF))

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

    def test_task_ref_only_is_idempotent_and_unknown_ref_is_clear(self):
        with tempfile.TemporaryDirectory() as d:
            engine = router_core.RouterEngine(Path(d))
            task = engine.begin_task(session_id="s", turn_id="t", prompt="Search code")
            route = engine.plan_route(task_ref=task["task_ref"])
            self.assertEqual(route["route_id"], task["route_id"])
            self.assertEqual(len(router_core._all_events(Path(d))), 1)
            with self.assertRaisesRegex(ValueError, "unknown task_ref"):
                engine.plan_route(task_ref="a" * 32)

    def test_stale_lock_recovers_without_removing_live_owner(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "state"
            lock = target.with_name(target.name + ".lock")
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(
                json.dumps({"pid": 99999999, "token": "stale", "created": 0})
            )
            with router_core._file_lock(target, timeout_seconds=0.3, stale_seconds=0):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())
            lock.write_text(
                json.dumps(
                    {"pid": os.getpid(), "token": "live", "created": time.time() - 60}
                )
            )
            with self.assertRaises(TimeoutError), router_core._file_lock(
                target, timeout_seconds=0.05, stale_seconds=0
            ):
                pass
            self.assertEqual(json.loads(lock.read_text())["token"], "live")
            lock.unlink()

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
    def test_outcome_v3_derives_single_axis_and_confounded_failure_axes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effort_plan = router_core.make_route_plan(
                "Implement from frozen spec", task_state="frozen", root=root
            )
            router_core.create_route_record(effort_plan, "task", root=root)
            effort = router_core.record_outcome(
                effort_plan.route_id,
                "escalated",
                confidence=0.9,
                verified=True,
                model_fit="adequate",
                effort_fit="under",
                context_fit="adequate",
                tool_data_fit="adequate",
                replacement_role=effort_plan.role,
                replacement_model=effort_plan.model,
                replacement_effort="xhigh",
                root=root,
            )
            self.assertEqual(effort["schema_version"], 3)
            self.assertEqual(effort["failure_axis"], "reasoning_budget")
            self.assertEqual(effort["route_fit"], "under_routed")

            model_plan = router_core.make_route_plan(
                "Search code", route_id=None, root=root
            )
            router_core.create_route_record(model_plan, "task2", root=root)
            model = router_core.record_outcome(
                model_plan.route_id,
                "escalated",
                confidence=0.9,
                model_fit="under",
                effort_fit="adequate",
                replacement_role=model_plan.role,
                replacement_model="gpt-5.6-terra",
                replacement_effort=model_plan.reasoning_effort,
                root=root,
            )
            self.assertEqual(model["failure_axis"], "model_capability")

            mixed_plan = router_core.make_route_plan(
                "Search code for refs", root=root
            )
            router_core.create_route_record(mixed_plan, "task3", root=root)
            mixed = router_core.record_outcome(
                mixed_plan.route_id,
                "escalated",
                confidence=0.9,
                model_fit="under",
                effort_fit="under",
                replacement_role=mixed_plan.role,
                replacement_model="gpt-5.6-terra",
                replacement_effort="high",
                root=root,
            )
            self.assertEqual(mixed["failure_axis"], "confounded")

            context_plan = router_core.make_route_plan("Search code", root=root)
            router_core.create_route_record(context_plan, "task4", root=root)
            context = router_core.record_outcome(
                context_plan.route_id,
                "failed",
                confidence=0.9,
                model_fit="under",
                effort_fit="under",
                context_fit="deficient",
                replacement_role=context_plan.role,
                replacement_model="gpt-5.6-terra",
                replacement_effort="high",
                root=root,
            )
            self.assertEqual(context["failure_axis"], "context")

    def test_v2_and_v3_evidence_validate_together_and_metrics_expand(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = router_core.RouterEngine(root)
            task = engine.begin_task(session_id="s", turn_id="t", prompt="Search code")
            outcome = engine.finalize_task(
                task["task_ref"],
                quality_gate="passed",
                verified=True,
                confidence=0.9,
                model_fit="adequate",
                effort_fit="adequate",
                context_fit="adequate",
                tool_data_fit="adequate",
            )
            self.assertTrue(all(event["schema_version"] == 3 for event in router_core._all_events(root)))
            legacy = json.loads(json.dumps(outcome))
            legacy["schema_version"] = 2
            for field in (
                "stage", "model_fit", "effort_fit", "context_fit", "tool_data_fit", "failure_axis"
            ):
                legacy.pop(field, None)
            router_core.validate_evidence_event(legacy)
            metrics = router_core.router_metrics(root)
            for key in (
                "task_class_model_effort_success",
                "model_fit_counts",
                "effort_fit_counts",
                "floor_violations",
                "decision_leakage",
                "mechanical_sol_share",
                "stage_handoff_success",
                "quality_adjusted_resource_bands",
                "model_effort_interaction_comparable",
            ):
                self.assertIn(key, metrics)

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
                model_fit="adequate",
                effort_fit="under",
                replacement_role=plan.role,
                replacement_model=plan.model,
                replacement_effort="xhigh",
                root=root,
            )

    def test_objective_gate_axis_proposals_metrics_and_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._evidence(root)
            proposals = router_core.learning_proposals(root)
            self.assertEqual({x["axis"] for x in proposals}, {"reasoning_effort"})
            self.assertTrue(all(x["status"] == "ready_for_shadow" for x in proposals))
            metrics = router_core.router_metrics(root)
            self.assertEqual(metrics["route_success"], 1.0)
            self.assertIsNotNone(metrics["route_success_wilson_95"]["low"])
            proposal = proposals[0]
            router_core.start_shadow(proposal["proposal_id"], root)
            engine = router_core.RouterEngine(root)
            for i in range(10):
                task = engine.begin_task(
                    session_id=f"shadow-{i}",
                    turn_id="1",
                    prompt="Implement from frozen spec",
                    project="p",
                    decision_features={
                        "cognitive_type": "implementation",
                        "spec_state": "frozen",
                        "confidence": 0.9,
                    },
                )
                engine.finalize_task(
                    task["task_ref"],
                    status="escalated",
                    quality_gate="passed",
                    route_fit="under_routed",
                    confidence=0.9,
                    verified=True,
                    objective_verification=True,
                    user_confirmed=True,
                    model_fit="adequate",
                    effort_fit="under",
                    replacement_role=task["route"]["role"],
                    replacement_model=task["route"]["model"],
                    replacement_effort="xhigh",
                )
            with self.assertRaises(ValueError):
                router_core.confirm_policy_change(proposal["proposal_id"], False, root)
            override = router_core.confirm_policy_change(
                proposal["proposal_id"], True, root
            )
            self.assertEqual(override["axis"], "reasoning_effort")

    def test_legacy_manual_shadow_observations_never_make_proposal_ready(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._evidence(root)
            proposal = router_core.learning_proposals(root)[0]
            router_core.start_shadow(proposal["proposal_id"], root)
            for _ in range(12):
                router_core.record_shadow_observation(proposal["proposal_id"], True, root)
            item = router_core.load_shadows(root)["items"][proposal["proposal_id"]]
            self.assertEqual(item["state"], "active")
            self.assertTrue(
                all(x["result"] == "inconclusive" for x in item["observations"])
            )
            self.assertEqual(
                next(
                    x
                    for x in router_core.learning_proposals(root)
                    if x["proposal_id"] == proposal["proposal_id"]
                )["status"],
                "shadow_running",
            )

    def test_concurrent_shadow_read_modify_write_preserves_every_observation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._evidence(root)
            proposal = router_core.learning_proposals(root)[0]
            router_core.start_shadow(proposal["proposal_id"], root)
            threads = [
                threading.Thread(
                    target=router_core.record_shadow_observation,
                    args=(proposal["proposal_id"], True, root),
                )
                for _ in range(20)
            ]
            [thread.start() for thread in threads]
            [thread.join() for thread in threads]
            item = router_core.load_shadows(root)["items"][proposal["proposal_id"]]
            self.assertEqual(len(item["observations"]), 20)
            self.assertEqual(item["state"], "active")

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
                    model_fit="adequate",
                    effort_fit="under",
                    replacement_role=plan.role,
                    replacement_model=plan.model,
                    replacement_effort="xhigh",
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
                    replacement_model="gpt-5.6-sol",
                    replacement_effort="low",
                    root=root,
                )
            proposal = next(
                x
                for x in router_core.learning_proposals(root)
                if x["axis"] == "reasoning_effort"
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
