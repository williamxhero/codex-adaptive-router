"""Static and behavioral checks for the Codex Adaptive Router plugin."""

from __future__ import annotations

import compileall
import json
import re
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import router_core


RELEASE_VERSION = "1.3.0"
CACHEBUSTER_VERSION = re.compile(
    rf"^{re.escape(RELEASE_VERSION)}\+codex\.\d{{14}}$"
)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def main() -> int:
    manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    version = manifest.get("version")
    if manifest.get("name") != "codex-adaptive-router" or not isinstance(version, str):
        raise ValueError("manifest identity/version is invalid")
    if version != RELEASE_VERSION and CACHEBUSTER_VERSION.fullmatch(version) is None:
        raise ValueError("manifest identity/version is invalid")
    mcp = load_json(PLUGIN_ROOT / ".mcp.json")
    router_mcp = mcp.get("mcpServers", {}).get("adaptive_router")
    if not isinstance(router_mcp, dict):
        raise TypeError("adaptive_router MCP server is missing")
    if router_mcp.get("cwd") != "." or router_mcp.get("args") != [
        "./scripts/router_mcp.py"
    ]:
        raise ValueError(
            "adaptive_router MCP server must use plugin-relative working directory"
        )
    hook_configuration = load_json(PLUGIN_ROOT / "hooks" / "hooks.json")
    registered_hooks = hook_configuration.get("hooks", {})
    for event in ("PostToolUse", "SubagentStop", "Stop"):
        hooks = [
            hook
            for registration in registered_hooks.get(event, [])
            for hook in registration.get("hooks", [])
        ]
        if not hooks:
            raise ValueError(f"{event} evidence hook is missing")
        if any(hook.get("timeout") != 3 for hook in hooks):
            raise ValueError(f"{event} evidence hooks must use a 3-second timeout")
        if any("async" in hook for hook in hooks):
            raise ValueError(
                f"{event} evidence hooks must be synchronous on current Codex"
            )
    for profile_name in router_core.available_profiles():
        profile = router_core.load_profile(profile_name)
        if profile.get("schema_version") != 4:
            raise ValueError(f"{profile_name} must use profile schema v4")
        token_policy = profile.get("token_policy", {})
        if (
            token_policy.get("estimate_source") != "profile_prior"
            or int(token_policy.get("minimum_direct_savings_tokens", -1)) < 0
            or not 0 <= float(token_policy.get("minimum_direct_savings_ratio", -1)) <= 1
        ):
            raise ValueError(f"{profile_name} has an invalid token policy")
        dispatch = profile.get("dispatch_policy", {})
        if dispatch != {
            "max_delegation_depth": 2,
            "default_active_specialists_per_parent": 1,
            "max_independent_read_only_children": 3,
            "single_writer_per_repository": True,
        }:
            raise ValueError(f"{profile_name} has an invalid dispatch policy")
        for role, config in profile["roles"].items():
            if config.get("default_model") not in router_core.VALID_MODELS:
                raise ValueError(f"{profile_name}/{role} uses an invalid model")
            effort = config.get("effort", {})
            if not isinstance(effort, dict) or any(
                effort.get(key) not in router_core.VALID_EFFORTS
                for key in ("min", "default", "max")
            ):
                raise ValueError(f"{profile_name}/{role} uses an invalid effort")
            authority = config.get("authority")
            floor = config.get("capability_floor")
            if floor != router_core.AUTHORITY_FLOORS.get(authority):
                raise ValueError(f"{profile_name}/{role} has an invalid capability floor")
            if any(
                router_core.MODEL_ORDER.get(model, 0)
                < router_core.MODEL_ORDER.get(floor, 99)
                for model in config.get("allowed_models", [])
            ):
                raise ValueError(f"{profile_name}/{role} allows a below-floor model")
            if config.get("access_mode") not in {"read_only", "writer"}:
                raise ValueError(f"{profile_name}/{role} has no access mode")
            if not set(config.get("allowed_execution_modes", [])) <= router_core.EXECUTION_TARGETS:
                raise ValueError(f"{profile_name}/{role} has invalid execution modes")
    if tomllib is not None:
        for agent in (PLUGIN_ROOT / "templates" / "agents").glob("*.toml"):
            value = tomllib.loads(agent.read_text(encoding="utf-8"))
            if value.get("model") not in router_core.VALID_MODELS:
                raise ValueError(f"{agent.name} has invalid model")
    if not compileall.compile_dir(str(PLUGIN_ROOT / "scripts"), quiet=1):
        raise ValueError("Python compilation failed")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        plan = router_core.make_route_plan("Run 500 parameter sweep tests", root=root)
        if plan.role != "router_experiment_runner":
            raise ValueError("routing smoke test failed")
        if plan.plan_version != 3 or plan.profile_version != 4 or not plan.stages:
            raise ValueError("Route Plan v3/Profile v4 smoke test failed")
        if plan.execution_target != "subagent" or not plan.dispatch_ready:
            raise ValueError("Thin Root dispatch smoke test failed")
    print("Adaptive Router validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
