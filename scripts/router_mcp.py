"""Small stdio MCP server exposing the local Adaptive Router policy engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import router_core


TOOLS = [
    {
        "name": "route_plan",
        "description": "Classify a Codex task by cognitive risk, select a profile, model, reasoning effort, and specialist role, then record a privacy-bounded route decision locally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task summary. It is hashed before persistence; raw text is not stored."},
                "profile": {"type": "string", "enum": ["generic", "quant"]},
                "task_state": {"type": "string", "enum": ["unknown", "frozen"], "description": "Use frozen only after the relevant specification is settled."},
                "force_role": {"type": "string", "description": "Optional explicit role override when the user or project contract requires one."},
                "session_id": {"type": "string", "description": "Optional session identifier; persisted only as a hash."},
                "project_fingerprint": {"type": "string", "description": "Optional stable project identifier; persisted only as a hash."},
                "record": {"type": "boolean", "default": True}
            },
            "required": ["task"],
            "additionalProperties": False
        }
    },
    {
        "name": "record_route_outcome",
        "description": "Record a privacy-bounded outcome for a previous route. Use this when verification, user correction, or an escalation gives evidence about whether the route was adequate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "route_id": {"type": "string"},
                "status": {"type": "string", "enum": ["completed", "verified", "failed", "corrected", "escalated", "overridden"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "verified": {"type": "boolean", "default": False},
                "replacement_role": {"type": "string"},
                "replacement_model": {"type": "string", "enum": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]},
                "replacement_effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh", "max", "ultra"]}
            },
            "required": ["route_id", "status", "confidence"],
            "additionalProperties": False
        }
    },
    {
        "name": "router_policy_status",
        "description": "Return local routing-policy revision, route/outcome counts, evidence proposals, and Gardener-compatible candidates. Does not expose raw prompts or paths.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}
    },
    {
        "name": "start_shadow_evaluation",
        "description": "Start a non-enforcing shadow evaluation for an evidence-backed route-policy proposal. The current policy remains active.",
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
            "additionalProperties": False
        }
    },
    {
        "name": "record_shadow_observation",
        "description": "Record whether one shadow recommendation would have been a better route. Two failures reject it; required successes validate it for user confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}, "success": {"type": "boolean"}},
            "required": ["proposal_id", "success"],
            "additionalProperties": False
        }
    },
    {
        "name": "confirm_policy_change",
        "description": "Apply a shadow-validated policy change. Requires an explicit confirmed_by_user=true value; the tool never auto-applies learning.",
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}, "confirmed_by_user": {"type": "boolean", "const": True}},
            "required": ["proposal_id", "confirmed_by_user"],
            "additionalProperties": False
        }
    }
]


def _result(value: Any) -> dict[str, Any]:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return {"content": [{"type": "text", "text": rendered}], "structuredContent": value}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_call(name: str, arguments: dict[str, Any]) -> Any:
    if name == "route_plan":
        task = str(arguments["task"])
        plan = router_core.make_route_plan(
            task,
            profile=arguments.get("profile"),
            task_state=str(arguments.get("task_state") or "unknown"),
            force_role=arguments.get("force_role"),
        )
        value = {key: getattr(plan, key) for key in plan.__dataclass_fields__}
        if arguments.get("record", True):
            value["record"] = router_core.create_route_record(
                plan,
                task,
                session_id=arguments.get("session_id"),
                project_fingerprint=arguments.get("project_fingerprint"),
            )
        return value
    if name == "record_route_outcome":
        return router_core.record_outcome(
            str(arguments["route_id"]),
            str(arguments["status"]),
            confidence=float(arguments["confidence"]),
            verified=bool(arguments.get("verified", False)),
            replacement_role=arguments.get("replacement_role"),
            replacement_model=arguments.get("replacement_model"),
            replacement_effort=arguments.get("replacement_effort"),
        )
    if name == "router_policy_status":
        return router_core.policy_status()
    if name == "start_shadow_evaluation":
        return router_core.start_shadow(str(arguments["proposal_id"]))
    if name == "record_shadow_observation":
        return router_core.record_shadow_observation(str(arguments["proposal_id"]), bool(arguments["success"]))
    if name == "confirm_policy_change":
        return router_core.confirm_policy_change(str(arguments["proposal_id"]), bool(arguments["confirmed_by_user"]))
    raise ValueError(f"unknown tool: {name}")


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "codex-adaptive-router", "version": "1.0.1"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            result = _tool_call(str(params.get("name") or ""), dict(params.get("arguments") or {}))
        except (KeyError, TypeError, ValueError, TimeoutError) as error:
            return _error(request_id, -32602, str(error))
        return {"jsonrpc": "2.0", "id": request_id, "result": _result(result)}
    return _error(request_id, -32601, f"method not found: {method}")


def main() -> int:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("MCP request must be an object")
            response = handle(request)
        except (ValueError, json.JSONDecodeError) as error:
            response = _error(None, -32700, str(error))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
