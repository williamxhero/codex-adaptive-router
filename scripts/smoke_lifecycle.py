"""Minimal real Route Plan v3 -> lease -> observed Outcome v4 lifecycle smoke."""

from __future__ import annotations

import tempfile
from pathlib import Path

import router_core


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        engine = router_core.RouterEngine(root)
        route = engine.plan_route(
            "Implement from frozen spec",
            task_state="frozen",
            project_fingerprint="lifecycle-smoke",
        )
        stage = next(item for item in route["stages"] if item["stage"] == "implement")
        lease = engine.dispatch_stage(
            route["task_ref"], stage["stage_id"], objective="implement bounded smoke"
        )
        engine.complete_stage(
            route["task_ref"], lease["lease_id"],
            success=True, quality_gate="passed",
            observed_role=stage["role"], observed_model=stage["model"],
            observed_effort=stage["reasoning_effort"],
            observed_execution_target=stage["execution_target"],
            observed_source="caller_supplied",
            boundary_status="passed", scope_status="passed",
            verification_status="passed",
        )
        outcome = engine.finalize_task(
            route["task_ref"],
            status="verified",
            quality_gate="passed",
            verified=True,
            objective_verification=True,
            stage="implement",
            lease_id=lease["lease_id"],
            boundary_status="passed",
            scope_status="passed",
            local_input_tokens=100,
            local_output_tokens=25,
            local_token_source="caller_supplied",
            local_token_complete=True,
        )
        public = router_core.project_public_evidence_event(outcome)
        router_core.validate_public_evidence_event(public)
        if outcome["plan_match"] != "matched" or "local_tokens" in public:
            raise ValueError("lifecycle smoke contract failed")
    print("Adaptive Router lifecycle smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
