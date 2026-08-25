"""Dependency-free stdio MCP surface for Router Engine v1.2.0."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import router_core

MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"]
BANDS = ["unknown", "low", "medium", "high", "very_high"]
TOOLS = [
    {
        "name": "route_plan",
        "description": "Return Route Plan v2 or idempotently confirm an existing task_ref. Decision Features v2 accepts any known subset, rejects unknown fields, and fills the rest heuristically. Capability floor is computed before independent effort; Sol retains final decision ownership.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "minLength": 1},
                "task_ref": {"type": "string", "pattern": "^[0-9a-f]{24,64}$"},
                "profile": {"type": "string", "enum": ["generic", "quant"]},
                "task_state": {
                    "type": "string",
                    "enum": ["unknown", "frozen"],
                    "description": "Use only 'unknown' until the relevant specification is settled; use 'frozen' after its semantics are fixed.",
                },
                "force_role": {"type": "string", "enum": sorted(router_core.VALID_ROLES)},
                "session_id": {"type": "string"},
                "project_fingerprint": {"type": "string"},
                "decision_features": {
                    "type": "object",
                    "properties": {
                        "operation_mode": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["operation_mode"]),
                        },
                        "scope": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["scope"]),
                        },
                        "spec_state": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["spec_state"]),
                        },
                        "reversibility": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["reversibility"]),
                        },
                        "cognitive_type": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["cognitive_type"]),
                        },
                        "risk_domains": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": sorted(router_core.KNOWN_RISK_DOMAINS),
                            },
                        },
                        "workload": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["workload"]),
                        },
                        "user_constraints": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [
                                    "role",
                                    "model",
                                    "reasoning_effort",
                                    "no_delegation",
                                ],
                            },
                        },
                        "feature_source": {
                            "type": "string",
                            "enum": [
                                "structured_heuristic",
                                "caller_supplied",
                                "legacy_v1",
                            ],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "feature_version": {"type": "integer", "const": 2},
                        "verification_depth": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["verification_depth"]),
                        },
                        "evidence_state": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["evidence_state"]),
                        },
                        "decision_impact": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["decision_impact"]),
                        },
                        "novelty": {
                            "type": "string",
                            "enum": sorted(router_core.FEATURE_VALUES["novelty"]),
                        },
                    },
                    "additionalProperties": False,
                },
                "constraints": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "enum": sorted(router_core.VALID_ROLES)},
                        "model": {"type": "string", "enum": MODELS},
                        "reasoning_effort": {"type": "string", "enum": EFFORTS},
                        "no_delegation": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "record": {"type": "boolean", "default": True},
            },
            "anyOf": [{"required": ["task"]}, {"required": ["task_ref"]}],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_route_outcome",
        "description": "Record v1-compatible or rich v2 outcome evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "route_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": sorted(router_core.OUTCOME_STATUSES),
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "verified": {"type": "boolean"},
                "quality_gate": {
                    "type": "string",
                    "enum": sorted(router_core.QUALITY_GATES),
                },
                "route_fit": {"type": "string", "enum": sorted(router_core.ROUTE_FITS)},
                "verification_kinds": {"type": "array", "items": {"type": "string"}},
                "objective_verification": {"type": "boolean"},
                "user_confirmed": {"type": "boolean"},
                "high_risk_regression": {"type": "boolean"},
                "replacement_role": {"type": "string"},
                "replacement_model": {"type": "string", "enum": MODELS},
                "replacement_effort": {"type": "string", "enum": EFFORTS},
                "token_band": {"type": "string", "enum": BANDS},
                "cost_band": {"type": "string", "enum": BANDS},
            },
            "required": ["route_id", "status", "confidence"],
            "additionalProperties": False,
        },
    },
    {
        "name": "router_policy_status",
        "description": "Return coverage, metrics, proposals, and shadow state.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "router_metrics",
        "description": "Return recomputable Outcome Intelligence metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "start_shadow_evaluation",
        "description": "Start non-enforcing counterfactual shadow evaluation.",
        "inputSchema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_shadow_observation",
        "description": "Backward-compatible manual shadow evidence channel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "success": {"type": "boolean"},
            },
            "required": ["proposal_id", "success"],
            "additionalProperties": False,
        },
    },
    {
        "name": "confirm_policy_change",
        "description": "Apply a validated axis-specific proposal only after explicit user confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "confirmed_by_user": {"type": "boolean", "const": True},
            },
            "required": ["proposal_id", "confirmed_by_user"],
            "additionalProperties": False,
        },
    },
]


def _result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            }
        ],
        "structuredContent": value,
    }


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_call(name: str, a: dict[str, Any]) -> Any:
    if name == "route_plan":
        return router_core.RouterEngine().plan_route(
            a.get("task"),
            task_ref=a.get("task_ref"),
            session_id=a.get("session_id"),
            project_fingerprint=a.get("project_fingerprint"),
            profile=a.get("profile"),
            task_state=str(a.get("task_state") or "unknown"),
            force_role=a.get("force_role"),
            decision_features=a.get("decision_features"),
            constraints=a.get("constraints"),
            record=bool(a.get("record", True)),
        )
    if name == "record_route_outcome":
        return router_core.record_outcome(
            str(a["route_id"]),
            str(a["status"]),
            confidence=float(a["confidence"]),
            verified=bool(a.get("verified", False)),
            quality_gate=a.get("quality_gate"),
            route_fit=str(a.get("route_fit") or "unknown"),
            verification_kinds=a.get("verification_kinds"),
            objective_verification=bool(a.get("objective_verification", False)),
            user_confirmed=bool(a.get("user_confirmed", False)),
            high_risk_regression=bool(a.get("high_risk_regression", False)),
            replacement_role=a.get("replacement_role"),
            replacement_model=a.get("replacement_model"),
            replacement_effort=a.get("replacement_effort"),
            token_band=str(a.get("token_band") or "unknown"),
            cost_band=str(a.get("cost_band") or "unknown"),
        )
    if name == "router_policy_status":
        return router_core.policy_status()
    if name == "router_metrics":
        return router_core.router_metrics()
    if name == "start_shadow_evaluation":
        return router_core.start_shadow(str(a["proposal_id"]))
    if name == "record_shadow_observation":
        return router_core.record_shadow_observation(
            str(a["proposal_id"]), bool(a["success"])
        )
    if name == "confirm_policy_change":
        return router_core.confirm_policy_change(
            str(a["proposal_id"]), bool(a["confirmed_by_user"])
        )
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
                "serverInfo": {"name": "codex-adaptive-router", "version": "1.2.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            value = _tool_call(
                str(params.get("name") or ""), dict(params.get("arguments") or {})
            )
        except (KeyError, TypeError, ValueError, TimeoutError) as error:
            return _error(request_id, -32602, str(error))
        return {"jsonrpc": "2.0", "id": request_id, "result": _result(value)}
    return _error(request_id, -32601, f"method not found: {method}")


def main() -> int:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise TypeError("MCP request must be an object")
            response = handle(request)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            response = _error(None, -32700, str(error))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
