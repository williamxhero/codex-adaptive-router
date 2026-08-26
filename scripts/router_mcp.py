"""Dependency-free stdio MCP surface for Router Engine v1.3.0."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import router_core

PLUGIN_VERSION = json.loads(
    (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
)["version"]

MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"]
BANDS = ["unknown", "low", "medium", "high", "very_high"]
TOOLS = [
    {
        "name": "route_plan",
        "description": "Return Route Plan v3 or idempotently confirm an existing task_ref. Decision Features v2, capability floor, and quality gates are applied before token-aware direct/subagent/visible-task selection; blocked complex work never silently falls back to Root.",
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
                "routing_context": {
                    "type": "object",
                    "properties": {
                        "delegation_depth": {"type": "integer", "minimum": 0, "maximum": 2},
                        "caller_is_root": {"type": "boolean"},
                        "worker_available": {"type": "boolean"},
                        "visible_task_available": {"type": "boolean"},
                        "context_isolation_required": {"type": "boolean"},
                        "cross_project": {"type": "boolean"},
                        "long_running": {"type": "boolean"},
                        "writer_required": {"type": "boolean"},
                        "estimated_direct_tokens": {"type": "integer", "minimum": 0},
                        "estimated_worker_tokens": {"type": "integer", "minimum": 0},
                        "estimated_handoff_tokens": {"type": "integer", "minimum": 0},
                        "estimated_acceptance_tokens": {"type": "integer", "minimum": 0},
                        "parent_lease_id": {"type": "string", "pattern": "^[0-9a-f]{24,64}$"}
                    },
                    "additionalProperties": False
                },
                "record": {"type": "boolean", "default": True},
            },
            "anyOf": [{"required": ["task"]}, {"required": ["task_ref"]}],
            "additionalProperties": False,
        },
    },
    {
        "name": "record_route_outcome",
        "description": "Record local Outcome Intelligence v4 evidence with planned-vs-observed provenance, lease state, exact local token usage when a stable source supplies it, and privacy-bounded outcome classifications.",
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
                "objective_verification": {
                    "type": "boolean",
                    "description": "True only for explicit or privacy-bounded objective verification evidence; agent stop alone is never verification.",
                },
                "user_confirmed": {"type": "boolean"},
                "high_risk_regression": {"type": "boolean"},
                "replacement_role": {"type": "string"},
                "replacement_model": {"type": "string", "enum": MODELS},
                "replacement_effort": {"type": "string", "enum": EFFORTS},
                "token_band": {"type": "string", "enum": BANDS},
                "cost_band": {"type": "string", "enum": BANDS},
                "stage": {
                    "type": "string",
                    "enum": sorted(router_core.STAGE_NAMES),
                    "description": "Optional explicit completed stage. If omitted, the Router may use one uniquely matched recent completed agent lifecycle stage; ambiguity remains unknown.",
                },
                "model_fit": {"type": "string", "enum": sorted(router_core.MODEL_EFFORT_FITS)},
                "effort_fit": {"type": "string", "enum": sorted(router_core.MODEL_EFFORT_FITS)},
                "context_fit": {"type": "string", "enum": sorted(router_core.CONTEXT_TOOL_FITS)},
                "tool_data_fit": {"type": "string", "enum": sorted(router_core.CONTEXT_TOOL_FITS)},
                "failure_axis": {"type": "string", "enum": sorted(router_core.FAILURE_AXES)},
                "result_signal": {
                    "type": "string",
                    "enum": sorted(router_core.RESULT_SIGNALS),
                    "default": "unknown",
                    "description": "Privacy-bounded outcome classification only; never send raw metrics or result text.",
                },
                "lease_id": {"type": "string", "pattern": "^[0-9a-f]{24,64}$"},
                "observed_role": {"type": "string", "enum": sorted(router_core.VALID_ROLES)},
                "observed_model": {"type": "string", "enum": MODELS},
                "observed_effort": {"type": "string", "enum": EFFORTS},
                "observed_execution_target": {"type": "string", "enum": sorted(router_core.EXECUTION_TARGETS)},
                "boundary_status": {"type": "string", "enum": sorted(router_core.BOUNDARY_STATUSES)},
                "scope_status": {"type": "string", "enum": sorted(router_core.SCOPE_STATUSES)},
                "archive_status": {"type": "string", "enum": sorted(router_core.ARCHIVE_STATUSES)},
                "local_input_tokens": {"type": "integer", "minimum": 0},
                "local_output_tokens": {"type": "integer", "minimum": 0},
                "local_token_source": {"type": "string", "enum": ["provider_usage", "codex_usage", "caller_supplied"]},
                "local_token_complete": {"type": "boolean"},
            },
            "required": ["route_id", "status", "confidence"],
            "additionalProperties": False,
        },
    },
    {
        "name": "claim_stage",
        "description": "Atomically claim a planned delegated stage lease. Enforces depth <=2, one writer per repository tree, default one specialist per parent, at most three explicitly independent read-only children, and Root-only visible-task titles.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_ref": {"type": "string", "pattern": "^[0-9a-f]{24,64}$"},
                "stage_id": {"type": "string"},
                "parent_lease_id": {"type": "string", "pattern": "^[0-9a-f]{24,64}$"},
                "independent_read_only": {"type": "boolean"},
                "independence_key": {"type": "string", "minLength": 1, "maxLength": 120},
                "caller_is_root": {"type": "boolean"},
                "worker_available": {"type": "boolean"},
                "visible_task_available": {"type": "boolean"},
                "visible_task_title": {"type": "string", "maxLength": 110},
                "objective": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            "required": ["task_ref", "stage_id", "caller_is_root", "worker_available", "visible_task_available"],
            "additionalProperties": False
        }
    },
    {
        "name": "transition_stage",
        "description": "Complete, fail, freeze-and-reroute, or release one stage lease without rewriting the immutable Route Plan v3.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_ref": {"type": "string", "pattern": "^[0-9a-f]{24,64}$"},
                "lease_id": {"type": "string", "pattern": "^[0-9a-f]{24,64}$"},
                "action": {"type": "string", "enum": ["complete", "fail", "freeze", "reroute", "release", "archive_observed"]},
                "quality_gate": {"type": "string", "enum": sorted(router_core.QUALITY_GATES)},
                "remaining_task": {"type": "string", "minLength": 1},
                "observed_role": {"type": "string", "enum": sorted(router_core.VALID_ROLES)},
                "observed_model": {"type": "string", "enum": MODELS},
                "observed_effort": {"type": "string", "enum": EFFORTS},
                "observed_execution_target": {"type": "string", "enum": sorted(router_core.EXECUTION_TARGETS)},
                "observed_source": {"type": "string", "enum": ["caller_supplied", "provider_hook"]},
                "boundary_status": {"type": "string", "enum": sorted(router_core.BOUNDARY_STATUSES)},
                "scope_status": {"type": "string", "enum": sorted(router_core.SCOPE_STATUSES)},
                "verification_status": {"type": "string", "enum": sorted(router_core.VERIFICATION_STATUSES)}
            },
            "required": ["task_ref", "lease_id", "action"],
            "additionalProperties": False
        }
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
            routing_context=a.get("routing_context"),
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
            stage=a.get("stage"),
            model_fit=str(a.get("model_fit") or "unknown"),
            effort_fit=str(a.get("effort_fit") or "unknown"),
            context_fit=str(a.get("context_fit") or "unknown"),
            tool_data_fit=str(a.get("tool_data_fit") or "unknown"),
            failure_axis=a.get("failure_axis"),
            result_signal=str(a.get("result_signal") or "unknown"),
            lease_id=a.get("lease_id"),
            observed_role=a.get("observed_role"),
            observed_model=a.get("observed_model"),
            observed_effort=a.get("observed_effort"),
            observed_execution_target=a.get("observed_execution_target"),
            boundary_status=str(a.get("boundary_status") or "unknown"),
            scope_status=str(a.get("scope_status") or "unknown"),
            archive_status=a.get("archive_status"),
            local_input_tokens=a.get("local_input_tokens"),
            local_output_tokens=a.get("local_output_tokens"),
            local_token_source=a.get("local_token_source"),
            local_token_complete=bool(a.get("local_token_complete", False)),
        )
    if name == "claim_stage":
        return router_core.RouterEngine().dispatch_stage(
            str(a["task_ref"]),
            str(a["stage_id"]),
            parent_lease_id=a.get("parent_lease_id"),
            independent_read_only=bool(a.get("independent_read_only", False)),
            independence_key=a.get("independence_key"),
            caller_is_root=bool(a["caller_is_root"]),
            worker_available=bool(a["worker_available"]),
            visible_task_available=bool(a["visible_task_available"]),
            visible_task_title=a.get("visible_task_title"),
            objective=a.get("objective"),
        )
    if name == "transition_stage":
        return router_core.RouterEngine().transition_stage(
            str(a["task_ref"]),
            str(a["lease_id"]),
            str(a["action"]),
            quality_gate=str(a.get("quality_gate") or "unknown"),
            remaining_task=a.get("remaining_task"),
            observed_role=a.get("observed_role"),
            observed_model=a.get("observed_model"),
            observed_effort=a.get("observed_effort"),
            observed_execution_target=a.get("observed_execution_target"),
            observed_source=a.get("observed_source"),
            boundary_status=str(a.get("boundary_status") or "unknown"),
            scope_status=str(a.get("scope_status") or "unknown"),
            verification_status=str(a.get("verification_status") or "unknown"),
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
                "serverInfo": {"name": "codex-adaptive-router", "version": PLUGIN_VERSION},
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
