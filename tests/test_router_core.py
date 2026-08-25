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
    def test_direct_stages_are_root_sol_medium_and_high_decisions_use_specialists(self):
        tiny = router_core.make_route_plan(
            "Rename x",
            decision_features={
                "cognitive_type": "direct",
                "scope": "tiny",
                "verification_depth": "basic",
            },
        )
        research = router_core.make_route_plan(
            "Research conflicting evidence",
            decision_features={
                "cognitive_type": "research",
                "evidence_state": "conflicting",
            },
        )
        architecture = router_core.make_route_plan(
            "Design an irreversible architecture",
            decision_features={
                "cognitive_type": "architecture",
                "reversibility": "irreversible",
            },
        )
        for plan in (tiny, research, architecture):
            for stage in plan.stages:
                if stage["role"] == "direct":
                    self.assertEqual(
                        (stage["model"], stage["reasoning_effort"]),
                        ("gpt-5.6-sol", "medium"),
                    )
        self.assertEqual(
            (tiny.route_mode, len(tiny.stages), tiny.stages[0]["role"]),
            ("single", 1, "direct"),
        )
        self.assertTrue(
            all(
                stage["role"] == "router_researcher"
                for stage in research.stages
                if stage["stage"] in {"frame", "synthesize"}
            )
        )
        self.assertTrue(
            all(
                stage["role"] == "router_architect"
                for stage in architecture.stages
                if stage["stage"] in {"frame", "synthesize"}
            )
        )
        self.assertEqual(
            (architecture.stages[-1]["stage"], architecture.stages[-1]["role"], architecture.stages[-1]["reasoning_effort"]),
            ("audit", "router_adversarial_auditor", "xhigh"),
        )

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

    def test_constraints_respect_root_fixed_effort_and_max_is_not_automatic(self):
        plan = router_core.make_route_plan(
            "small answer",
            constraints={"model": "gpt-5.6-sol", "reasoning_effort": "max"},
        )
        self.assertEqual(plan.reasoning_effort, "medium")
        self.assertEqual(plan.stages[0]["reasoning_effort"], "medium")
        specialist = router_core.make_route_plan(
            "Research the evidence",
            decision_features={"cognitive_type": "research"},
            constraints={"reasoning_effort": "max"},
        )
        self.assertEqual(specialist.reasoning_effort, "max")
        self.assertTrue(
            all(
                stage["reasoning_effort"] == "max"
                for stage in specialist.stages
                if stage["role"] == "router_researcher"
            )
        )
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

    def test_luna_high_and_terra_xhigh_never_receive_decision_authority(self):
        luna = router_core.make_route_plan(
            "Research the architecture",
            decision_features={"cognitive_type": "architecture"},
            constraints={
                "role": "router_code_mapper",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "high",
            },
        )
        terra = router_core.make_route_plan(
            "Research the architecture",
            decision_features={"cognitive_type": "research"},
            constraints={"model": "gpt-5.6-terra", "reasoning_effort": "xhigh"},
        )
        for plan in (luna, terra):
            self.assertTrue(
                all(
                    stage["model"] == "gpt-5.6-sol"
                    for stage in plan.stages
                    if stage["authority"] in {"decision", "audit"}
                )
            )
        self.assertEqual(terra.model, "gpt-5.6-sol")



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

    def test_task_correlation_waits_for_complete_identity_salt_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = router_core.RouterEngine(root)
            salt = router_core.salt_path(root)
            callers_ready = threading.Barrier(4)
            writer_opened = threading.Event()
            writer_closed = threading.Event()
            competing_reader = threading.Event()
            real_token_bytes = router_core.secrets.token_bytes
            real_fdopen = router_core.os.fdopen
            real_read_bytes = Path.read_bytes
            token_call_count = 0
            token_call_lock = threading.Lock()

            def synchronized_token_bytes(size):
                nonlocal token_call_count
                with token_call_lock:
                    token_call_count += 1
                    should_wait = token_call_count <= callers_ready.parties
                if should_wait:
                    callers_ready.wait(timeout=2)
                return real_token_bytes(size)

            def observed_read_bytes(path):
                if (
                    path == salt
                    and writer_opened.is_set()
                    and not writer_closed.is_set()
                ):
                    competing_reader.set()
                    return b""
                return real_read_bytes(path)

            class DelayedSaltWriter:
                def __init__(self, handle):
                    self.handle = handle

                def __getattr__(self, name):
                    return getattr(self.handle, name)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_value, traceback):
                    try:
                        return self.handle.__exit__(exc_type, exc_value, traceback)
                    finally:
                        writer_closed.set()

                def write(self, value):
                    writer_opened.set()
                    competing_reader.wait(timeout=1)
                    return self.handle.write(value)

            def delayed_fdopen(descriptor, *args, **kwargs):
                return DelayedSaltWriter(real_fdopen(descriptor, *args, **kwargs))

            results = []
            errors = []

            def begin_same_task():
                try:
                    results.append(
                        engine.begin_task(
                            session_id="s", turn_id="t", prompt="Search code"
                        )
                    )
                except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                    errors.append(error)

            with (
                mock.patch.object(
                    router_core.secrets,
                    "token_bytes",
                    side_effect=synchronized_token_bytes,
                ),
                mock.patch.object(router_core.os, "fdopen", side_effect=delayed_fdopen),
                mock.patch.object(Path, "read_bytes", observed_read_bytes),
            ):
                threads = [
                    threading.Thread(target=begin_same_task)
                    for _ in range(4)
                ]
                [thread.start() for thread in threads]
                [thread.join() for thread in threads]

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 4)
            self.assertEqual(len({result["task_ref"] for result in results}), 1)
            self.assertEqual(len(router_core._all_events(root)), 1)
            self.assertGreaterEqual(len(salt.read_bytes()), 32)

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

    def test_lifecycle_uniquely_associates_stage_without_raw_agent_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = router_core.RouterEngine(root)
            task = engine.begin_task(
                session_id="s",
                turn_id="t",
                prompt="Implement the frozen change",
                decision_features={
                    "cognitive_type": "implementation",
                    "spec_state": "frozen",
                },
            )
            agent_id = "raw-agent-identity-must-not-persist"
            lifecycle = {
                "session_id": "s",
                "turn_id": "t",
                "agent_id": agent_id,
                "agent_type": "router_research_engineer",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
            }
            spawn = {
                "session_id": "s",
                "turn_id": "t",
                "tool_use_id": "spawn-call",
                "tool_name": "spawn_agent",
                "tool_input": {
                    "agent_type": "router_research_engineer",
                    "model": "gpt-5.6-terra",
                    "reasoning_effort": "high",
                },
                "tool_response": {"agent_id": agent_id},
            }
            engine.observe_event("PostToolUse", spawn)
            engine.observe_event("PostToolUse", spawn)
            for event in ("SubagentStart", "SubagentStart", "SubagentStop", "SubagentStop"):
                engine.observe_event(event, lifecycle)

            outcome = engine.finalize_task(
                task["task_ref"],
                status="completed",
                quality_gate="passed",
                objective_verification=False,
            )
            self.assertEqual(
                (outcome.get("stage"), outcome["stage_source"]),
                ("implement", "lifecycle_inferred"),
            )
            self.assertFalse(outcome["objective_verification"])

            ledger_text = router_core.ledger_path(root).read_text(encoding="utf-8")
            events_text = router_core.events_path(root).read_text(encoding="utf-8")
            self.assertNotIn(agent_id, ledger_text)
            self.assertNotIn(agent_id, events_text)
            ledger = json.loads(ledger_text)
            lifecycle_map = ledger["tasks"][task["task_ref"]]["aggregate"]["lifecycle"]
            self.assertEqual(len(lifecycle_map), 1)
            agent_hash, state = next(iter(lifecycle_map.items()))
            self.assertEqual(len(agent_hash), 32)
            self.assertEqual(
                (state["stage"], state["status"]),
                ("implement", "completed"),
            )
            execution_events = [
                event
                for event in router_core._all_events(root)
                if event["type"] == "execution"
            ]
            self.assertEqual(len(execution_events), 3)

    def test_lifecycle_ambiguous_or_unmatched_stage_stays_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = router_core.RouterEngine(root)
            task = engine.begin_task(
                session_id="s",
                turn_id="t",
                prompt="Research the conflicting evidence",
                decision_features={
                    "cognitive_type": "research",
                    "evidence_state": "conflicting",
                },
            )
            for agent_id, role, model, effort in (
                ("ambiguous", "router_researcher", "gpt-5.6-sol", "high"),
                ("unmatched", "worker", "gpt-5.6-terra", "medium"),
            ):
                payload = {
                    "session_id": "s",
                    "turn_id": "t",
                    "agent_id": agent_id,
                    "agent_type": role,
                    "model": model,
                    "reasoning_effort": effort,
                }
                engine.observe_event("SubagentStart", payload)
                engine.observe_event("SubagentStop", payload)

            outcome = engine.finalize_task(task["task_ref"])
            self.assertNotIn("stage", outcome)
            self.assertEqual(outcome["stage_source"], "unknown")
            ledger = json.loads(router_core.ledger_path(root).read_text())
            lifecycle = ledger["tasks"][task["task_ref"]]["aggregate"]["lifecycle"]
            self.assertEqual(
                {state["stage"] for state in lifecycle.values()},
                {"unknown"},
            )

            later_unknown = engine.begin_task(
                session_id="later-unknown",
                turn_id="t",
                prompt="Implement the frozen change",
                decision_features={
                    "cognitive_type": "implementation",
                    "spec_state": "frozen",
                },
            )
            for agent_id, role, model, effort in (
                ("known-first", "router_research_engineer", "gpt-5.6-terra", "high"),
                ("unknown-last", "worker", "gpt-5.6-terra", "medium"),
            ):
                payload = {
                    "session_id": "later-unknown",
                    "turn_id": "t",
                    "agent_id": agent_id,
                    "agent_type": role,
                    "model": model,
                    "reasoning_effort": effort,
                }
                engine.observe_event("SubagentStart", payload)
                engine.observe_event("SubagentStop", payload)
            later_outcome = engine.finalize_task(later_unknown["task_ref"])
            self.assertNotIn("stage", later_outcome)
            self.assertEqual(later_outcome["stage_source"], "unknown")

    def test_lifecycle_handoff_requires_objective_verification_on_both_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def record_implementation_task(session_id, implement_verified):
                engine = router_core.RouterEngine(root)
                task = engine.begin_task(
                    session_id=session_id,
                    turn_id="t",
                    prompt="Implement the frozen change",
                    decision_features={
                        "cognitive_type": "implementation",
                        "spec_state": "frozen",
                    },
                )
                for index, (role, model, effort, objective) in enumerate(
                    (
                        (
                            "router_research_engineer",
                            "gpt-5.6-terra",
                            "high",
                            implement_verified,
                        ),
                        (
                            "router_experiment_runner",
                            "gpt-5.6-luna",
                            "medium",
                            True,
                        ),
                    )
                ):
                    payload = {
                        "session_id": session_id,
                        "turn_id": "t",
                        "agent_id": f"{session_id}-{index}",
                        "agent_type": role,
                        "model": model,
                        "reasoning_effort": effort,
                    }
                    engine.observe_event("SubagentStart", payload)
                    engine.observe_event("SubagentStop", payload)
                    outcome = engine.finalize_task(
                        task["task_ref"],
                        quality_gate="passed",
                        verified=objective,
                        objective_verification=objective,
                    )
                    self.assertEqual(outcome["stage_source"], "lifecycle_inferred")

            record_implementation_task("unverified", False)
            self.assertEqual(
                router_core.router_metrics(root)["stage_handoff_success"]["denominator"],
                0,
            )
            record_implementation_task("verified", True)
            handoff = router_core.router_metrics(root)["stage_handoff_success"]
            self.assertEqual(
                (handoff["numerator"], handoff["denominator"], handoff["rate"]),
                (1, 1, 1.0),
            )

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
    def test_model_and_effort_fit_rates_use_axis_known_denominators(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = router_core.RouterEngine(root)
            fits = [
                ("under", "under"),
                ("over", "adequate"),
                ("unknown", "unknown"),
            ]
            for index, (model_fit, effort_fit) in enumerate(fits):
                task = engine.begin_task(
                    session_id=f"s{index}",
                    turn_id="1",
                    prompt="Search code",
                )
                engine.finalize_task(
                    task["task_ref"],
                    quality_gate="passed",
                    verified=True,
                    model_fit=model_fit,
                    effort_fit=effort_fit,
                )
            metrics = router_core.router_metrics(root)
            self.assertEqual(metrics["model_fit_counts"]["under"], 1)
            self.assertEqual(metrics["model_fit_counts"]["over"], 1)
            self.assertEqual(metrics["model_fit_denominator"], 2)
            self.assertEqual(metrics["model_under_routing_rate"], 0.5)
            self.assertEqual(metrics["model_over_routing_rate"], 0.5)
            self.assertEqual(metrics["effort_fit_denominator"], 2)
            self.assertEqual(metrics["effort_under_routing_rate"], 0.5)
            self.assertEqual(metrics["effort_over_routing_rate"], 0.0)

    def test_exceptional_positive_outcome_creates_idempotent_audit_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = router_core.RouterEngine(root)
            task = engine.begin_task(
                session_id="s",
                turn_id="t",
                prompt="Run the defined backtest",
                decision_features={"cognitive_type": "execution"},
            )
            original_stages = json.loads(json.dumps(task["route"]["stages"]))
            self.assertNotIn("audit", [stage["stage"] for stage in original_stages])
            first = engine.finalize_task(
                task["task_ref"],
                status="verified",
                quality_gate="passed",
                verified=True,
                objective_verification=True,
                confidence=0.95,
                result_signal="exceptional_positive",
            )
            second = engine.finalize_task(
                task["task_ref"],
                status="verified",
                quality_gate="passed",
                verified=True,
                objective_verification=True,
                confidence=0.95,
                result_signal="exceptional_positive",
            )
            followup = first["audit_followup"]
            self.assertEqual(first["event_id"], second["event_id"])
            self.assertEqual(
                (followup["stage"], followup["role"], followup["model"], followup["reasoning_effort"], followup["required"]),
                ("audit", "router_adversarial_auditor", "gpt-5.6-sol", "xhigh", True),
            )
            self.assertEqual(task["route"]["stages"], original_stages)
            outcomes = [
                event
                for event in router_core._all_events(root)
                if event["type"] == "outcome"
            ]
            self.assertEqual(len(outcomes), 1)
            self.assertNotIn("metric", json.dumps(first).casefold())

            audit_outcome = engine.finalize_task(
                task["task_ref"],
                stage="audit",
                status="verified",
                quality_gate="passed",
                verified=True,
                objective_verification=True,
                confidence=0.95,
            )
            self.assertEqual(audit_outcome["stage"], "audit")
            self.assertEqual(audit_outcome["stage_source"], "caller_supplied")
            self.assertNotIn("audit_followup", audit_outcome)

    def test_stage_validation_inference_and_verified_adjacent_handoffs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = router_core.RouterEngine(root)
            tiny = engine.begin_task(
                session_id="tiny",
                turn_id="1",
                prompt="Rename x",
                decision_features={
                    "cognitive_type": "direct",
                    "scope": "tiny",
                    "verification_depth": "basic",
                },
            )
            inferred = engine.finalize_task(
                tiny["task_ref"],
                verified=True,
                quality_gate="passed",
                objective_verification=True,
            )
            self.assertEqual(
                (inferred["stage"], inferred["stage_source"]),
                ("synthesize", "single_stage_inferred"),
            )

            staged = engine.begin_task(
                session_id="staged",
                turn_id="1",
                prompt="Implement the frozen change",
                decision_features={
                    "cognitive_type": "implementation",
                    "spec_state": "frozen",
                },
            )
            with self.assertRaisesRegex(ValueError, "stage is not part"):
                engine.finalize_task(staged["task_ref"], stage="audit")
            for stage, quality in (
                ("frame", "passed"),
                ("implement", "passed"),
                ("verify", "failed"),
            ):
                outcome = engine.finalize_task(
                    staged["task_ref"],
                    stage=stage,
                    status="verified" if quality == "passed" else "failed",
                    quality_gate=quality,
                    objective_verification=True,
                    verified=quality == "passed",
                )
                self.assertEqual(outcome["stage_source"], "caller_supplied")
            handoff = router_core.router_metrics(root)["stage_handoff_success"]
            self.assertEqual(
                handoff,
                {
                    "numerator": 1,
                    "denominator": 2,
                    "rate": 0.5,
                    "passed": 1,
                    "total": 2,
                },
            )

    def test_model_proposals_hold_role_and_effort_fixed_and_confounded_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as model_dir, tempfile.TemporaryDirectory() as mixed_dir:
            model_root = Path(model_dir)
            mixed_root = Path(mixed_dir)
            for index in range(5):
                for root, mixed in ((model_root, False), (mixed_root, True)):
                    plan = router_core.make_route_plan("Search code", root=root)
                    router_core.create_route_record(
                        plan,
                        "task",
                        session_id=f"s{index % 3}",
                        project_fingerprint="p",
                        root=root,
                    )
                    router_core.record_outcome(
                        plan.route_id,
                        "escalated",
                        confidence=0.9,
                        verified=True,
                        quality_gate="passed",
                        objective_verification=True,
                        user_confirmed=True,
                        model_fit="under",
                        effort_fit="under" if mixed else "adequate",
                        replacement_role=plan.role,
                        replacement_model="gpt-5.6-terra",
                        replacement_effort="high" if mixed else plan.reasoning_effort,
                        root=root,
                    )
            proposals = router_core.learning_proposals(model_root)
            self.assertEqual({proposal["axis"] for proposal in proposals}, {"model"})
            self.assertEqual(router_core.learning_proposals(mixed_root), [])

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
                "stage", "model_fit", "effort_fit", "context_fit", "tool_data_fit", "failure_axis",
                "result_signal", "stage_source", "audit_followup",
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
                plan = router_core.make_route_plan(
                    "Implement from frozen spec", task_state="frozen", root=root
                )
                router_core.create_route_record(
                    plan,
                    "Implement from frozen spec",
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
                    replacement_role=plan.role,
                    replacement_model=plan.model,
                    replacement_effort="medium",
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
                prompt="Implement from frozen spec",
                project="p3",
                decision_features={
                    "cognitive_type": "implementation",
                    "spec_state": "frozen",
                },
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
