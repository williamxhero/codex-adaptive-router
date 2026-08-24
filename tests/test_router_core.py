from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import router_core


class RouterPlanTests(unittest.TestCase):
    def test_search_routes_to_luna_mapper(self) -> None:
        plan = router_core.make_route_plan("Search code for the portfolio accounting implementation location.")
        self.assertEqual(plan.role, "router_code_mapper")
        self.assertEqual(plan.model, "gpt-5.6-luna")
        self.assertEqual(plan.reasoning_effort, "medium")

    def test_frozen_implementation_routes_to_terra(self) -> None:
        plan = router_core.make_route_plan(
            "Implement the fee model according to the existing specification.", task_state="frozen"
        )
        self.assertEqual(plan.role, "router_research_engineer")
        self.assertEqual(plan.model, "gpt-5.6-terra")
        self.assertEqual(plan.reasoning_effort, "high")

    def test_quant_anomaly_routes_to_sol_xhigh_auditor(self) -> None:
        plan = router_core.make_route_plan("This strategy Sharpe 3.4 and drawdown 6%; audit whether it is credible.")
        self.assertEqual(plan.profile, "quant")
        self.assertEqual(plan.role, "router_adversarial_auditor")
        self.assertEqual(plan.model, "gpt-5.6")
        self.assertEqual(plan.reasoning_effort, "xhigh")


class LearningTests(unittest.TestCase):
    def test_learning_requires_evidence_shadow_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            route_ids: list[str] = []
            for index in range(3):
                plan = router_core.make_route_plan(
                    "Implement the fee model according to the existing specification.", task_state="frozen", root=root
                )
                router_core.create_route_record(
                    plan,
                    "Implement the fee model according to the existing specification.",
                    session_id=f"session-{index}",
                    project_fingerprint=f"project-{index % 2}",
                    root=root,
                )
                route_ids.append(plan.route_id)
                router_core.record_outcome(
                    plan.route_id,
                    "escalated",
                    confidence=0.9,
                    replacement_role="router_researcher",
                    replacement_model="gpt-5.6",
                    replacement_effort="high",
                    root=root,
                )
            proposal = router_core.learning_proposals(root)[0]
            self.assertEqual(proposal["status"], "ready_for_shadow")
            self.assertEqual(proposal["scope"], "global")
            with self.assertRaises(ValueError):
                router_core.confirm_policy_change(proposal["proposal_id"], True, root)
            router_core.start_shadow(proposal["proposal_id"], root)
            for _ in range(5):
                router_core.record_shadow_observation(proposal["proposal_id"], True, root)
            confirmed = router_core.confirm_policy_change(proposal["proposal_id"], True, root)
            self.assertEqual(confirmed["to"]["model"], "gpt-5.6")
            updated = router_core.make_route_plan(
                "Implement the fee model according to the existing specification.", task_state="frozen", root=root
            )
            self.assertEqual(updated.role, "router_researcher")

    def test_route_record_does_not_store_raw_task_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = "private strategy secret must never be persisted verbatim"
            plan = router_core.make_route_plan(task, root=root)
            router_core.create_route_record(plan, task, root=root)
            raw = router_core.events_path(root).read_text(encoding="utf-8")
            self.assertNotIn(task, raw)
            self.assertIn("task_fingerprint", raw)


if __name__ == "__main__":
    unittest.main()
