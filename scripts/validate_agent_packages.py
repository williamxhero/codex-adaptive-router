"""Validate Profile v4 roles against shipped custom-agent packages."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError as error:  # pragma: no cover
    raise SystemExit("Python 3.11+ is required") from error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import router_core


def main() -> int:
    agents = {}
    for path in (ROOT / "templates" / "agents").glob("router_*.toml"):
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        agents[str(value.get("name"))] = (path, value)
    expected = router_core.VALID_ROLES - {"direct"}
    if set(agents) != expected:
        raise ValueError(
            f"agent package set mismatch; missing={sorted(expected - set(agents))}, "
            f"extra={sorted(set(agents) - expected)}"
        )
    for profile_name in router_core.available_profiles():
        profile = router_core.load_profile(profile_name)
        if profile.get("schema_version") != 4:
            raise ValueError(f"{profile_name} is not Profile v4")
        for role in expected:
            config = profile["roles"][role]
            path, package = agents[role]
            if package.get("model") != config["default_model"]:
                raise ValueError(f"{path.name} model disagrees with {profile_name}")
            if package.get("model_reasoning_effort") != config["effort"]["default"]:
                raise ValueError(f"{path.name} effort disagrees with {profile_name}")
            access = config.get("access_mode")
            if access == "read_only" and package.get("sandbox_mode") != "read-only":
                raise ValueError(f"{path.name} must be read-only")
            if access == "writer" and package.get("sandbox_mode") == "read-only":
                raise ValueError(f"{path.name} must be able to implement")
            if not config.get("allowed_execution_modes"):
                raise ValueError(f"{profile_name}/{role} has no execution modes")
    with tempfile.TemporaryDirectory() as directory:
        engine = router_core.RouterEngine(Path(directory))
        route = engine.plan_route(
            "Implement from frozen spec", task_state="frozen",
            project_fingerprint="agent-package-contract",
        )
        stage = next(item for item in route["stages"] if item["stage"] == "implement")
        claim = engine.dispatch_stage(
            route["task_ref"], stage["stage_id"], objective="bounded contract probe"
        )
        package = claim["agent_package"]
        required = {
            "objective", "stage_id", "lease_id", "parent_lease_id",
            "delegation_depth", "role", "model", "reasoning_effort",
            "authority", "access_mode", "ownership", "deliverable_contract",
            "verification_contract", "failure_disposition",
            "escalation_contract", "handback_contract",
        }
        if set(package) != required:
            raise ValueError("generated agent package fields are not strict")
        if package != {
            "objective": "bounded contract probe",
            "stage_id": stage["stage_id"],
            "lease_id": claim["lease_id"],
            "parent_lease_id": None,
            "delegation_depth": stage["delegation_depth"],
            "role": stage["role"],
            "model": stage["model"],
            "reasoning_effort": stage["reasoning_effort"],
            "authority": stage["authority"],
            "access_mode": stage["access_mode"],
            "ownership": "single_repository_writer",
            "deliverable_contract": "bounded_result",
            "verification_contract": "objective_quality_gate",
            "failure_disposition": "freeze_and_reroute",
            "escalation_contract": "freeze_and_reroute_on_scope_or_semantic_mismatch",
            "handback_contract": "return_to_parent_for_acceptance",
        }:
            raise ValueError("generated agent package contract is invalid")
    print("Agent package contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
