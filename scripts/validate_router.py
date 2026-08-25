"""Static and behavioral checks for the Codex Adaptive Router plugin."""

from __future__ import annotations

import compileall
import json
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


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def main() -> int:
    manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    if (
        manifest.get("name") != "codex-adaptive-router"
        or manifest.get("version") != "1.2.0"
    ):
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
    load_json(PLUGIN_ROOT / "hooks" / "hooks.json")
    for profile_name in router_core.available_profiles():
        profile = router_core.load_profile(profile_name)
        if profile.get("schema_version") != 3:
            raise ValueError(f"{profile_name} must use profile schema v3")
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
        if plan.plan_version != 2 or not plan.stages:
            raise ValueError("Route Plan v2 smoke test failed")
    print("Adaptive Router validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
