"""Privacy-bounded Outcome Intelligence engine for Codex Adaptive Router."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
VALID_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
OUTCOME_STATUSES = {
    "completed",
    "verified",
    "failed",
    "corrected",
    "escalated",
    "overridden",
}
TASK_STATES = {"unknown", "frozen"}
EXECUTION_TARGETS = {"direct", "subagent", "visible_task"}
EXECUTION_MODES = {"root", "delegated", "isolated"}
DISPATCH_BLOCKERS = {
    "none",
    "worker_unavailable",
    "visible_task_unavailable",
    "quality_floor_requires_specialist",
    "delegation_depth_exceeded",
    "parent_lease_required",
    "parent_lease_inactive",
    "read_only_concurrency_exceeded",
    "writer_lease_conflict",
    "independent_read_only_not_declared",
    "duplicate_independence_key",
    "visible_task_root_only",
    "visible_task_title_invalid",
}
WRITER_MODES = {"read_only", "single_writer"}
LEASE_STATUSES = {"active", "completed", "failed", "frozen", "released"}
BOUNDARY_STATUSES = {"unknown", "passed", "failed"}
SCOPE_STATUSES = {"unknown", "passed", "failed"}
VERIFICATION_STATUSES = {"unknown", "provisional", "passed", "failed"}
ARCHIVE_STATUSES = {
    "not_applicable", "not_ready", "eligible", "requested", "archived", "failed"
}
PLAN_MATCHES = {"unknown", "matched", "deviated"}
QUALITY_GATES = {"unknown", "provisional", "passed", "failed"}
ROUTE_FITS = {"unknown", "adequate", "under_routed", "over_routed"}
MODEL_EFFORT_FITS = {"under", "adequate", "over", "unknown"}
CONTEXT_TOOL_FITS = {"adequate", "deficient", "unknown"}
FAILURE_AXES = {
    "model_capability", "reasoning_budget", "context", "tool_data",
    "execution", "none", "confounded",
}
RESULT_SIGNALS = {"normal", "exceptional_positive", "exceptional_negative", "unknown"}
STAGE_SOURCES = {
    "caller_supplied",
    "lifecycle_inferred",
    "single_stage_inferred",
    "unknown",
}
BANDS = {"unknown", "low", "medium", "high", "very_high"}
FEATURE_VALUES = {
    "operation_mode": {
        "answer",
        "diagnose",
        "change",
        "review",
        "research",
        "execute",
        "monitor",
    },
    "scope": {"tiny", "bounded", "multi_file", "cross_system"},
    "spec_state": {"unknown", "ambiguous", "frozen"},
    "reversibility": {"reversible", "costly", "irreversible"},
    "cognitive_type": {
        "direct",
        "discovery",
        "execution",
        "implementation",
        "diagnosis",
        "research",
        "architecture",
        "audit",
        "exploration",
    },
    "workload": {"small", "medium", "large", "batch"},
    "verification_depth": {"basic", "standard", "deep", "adversarial"},
    "evidence_state": {"unknown", "consistent", "conflicting"},
    "decision_impact": {"low", "medium", "high", "critical"},
    "novelty": {"routine", "novel", "open_ended"},
}
MODEL_ORDER = {"gpt-5.6-luna": 1, "gpt-5.6-terra": 2, "gpt-5.6-sol": 3}
EFFORT_ORDER = {"low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5, "ultra": 6}
AUTHORITY_FLOORS = {
    "evidence": "gpt-5.6-luna",
    "implementation": "gpt-5.6-terra",
    "decision": "gpt-5.6-sol",
    "audit": "gpt-5.6-sol",
}
STAGE_NAMES = {"frame", "collect", "implement", "verify", "synthesize", "audit"}
AUTHORITIES = set(AUTHORITY_FLOORS)
EFFORT_BASES = {
    "mechanical_basic",
    "default",
    "broad_scope",
    "frozen_implementation",
    "deep_verification",
    "conflicting_evidence",
    "costly_impact",
    "audit",
    "open_ended",
    "irreversible_or_critical",
    "high_impact_exceptional_result",
    "explicit_constraint",
    "policy_override",
    "role_clamp",
}
KNOWN_RISK_DOMAINS = {
    "quantitative_research",
    "high_impact",
    "security",
    "privacy",
    "production",
    "financial",
    "legal",
    "medical",
}
VALID_ROLES = {
    "direct",
    "router_code_mapper",
    "router_experiment_runner",
    "router_research_engineer",
    "router_researcher",
    "router_quant_researcher",
    "router_architect",
    "router_adversarial_auditor",
    "router_strategy_scout",
}
MAX_DELEGATION_DEPTH = 2
MAX_READ_ONLY_CHILDREN = 3
VISIBLE_TASK_TITLE = re.compile(
    r"^\[AR\]\[(SOL|TERRA|LUNA)-(LOW|MEDIUM|HIGH|XHIGH|MAX|ULTRA)\] .{1,80}$"
)
VERIFICATION_KINDS = {"tests", "build", "static_validation", "review"}
HOOK_EVENTS = {
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SessionEnd",
    "ArchiveObserved",
}
HEX_ID = re.compile(r"^[0-9a-f]{24,64}$")
DEDUPE_ID = re.compile(r"^[A-Za-z0-9:._-]{1,180}$")


def _strict_object(value: Any, allowed: set[str], required: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    extra = set(value) - allowed
    missing = required - set(value)
    if extra or missing:
        raise ValueError(f"{name} has invalid fields; extra={sorted(extra)}, missing={sorted(missing)}")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_constraints(value: Any) -> dict[str, Any]:
    constraints = _strict_object(
        value or {}, {"role", "model", "reasoning_effort", "no_delegation"}, set(), "constraints"
    )
    if "role" in constraints and constraints["role"] not in VALID_ROLES:
        raise ValueError("constraints.role is invalid")
    if "model" in constraints and constraints["model"] not in VALID_MODELS:
        raise ValueError("constraints.model is invalid")
    if "reasoning_effort" in constraints and constraints["reasoning_effort"] not in VALID_EFFORTS:
        raise ValueError("constraints.reasoning_effort is invalid")
    if "no_delegation" in constraints and type(constraints["no_delegation"]) is not bool:
        raise ValueError("constraints.no_delegation must be boolean")
    return dict(constraints)


def validate_routing_context(value: Any) -> dict[str, Any]:
    context = _strict_object(
        value or {},
        {
            "delegation_depth",
            "caller_is_root",
            "worker_available",
            "visible_task_available",
            "context_isolation_required",
            "cross_project",
            "long_running",
            "writer_required",
            "estimated_direct_tokens",
            "estimated_worker_tokens",
            "estimated_handoff_tokens",
            "estimated_acceptance_tokens",
            "parent_lease_id",
            "independent_read_only_count",
        },
        set(),
        "routing_context",
    )
    depth = context.get("delegation_depth", 0)
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= MAX_DELEGATION_DEPTH:
        raise ValueError("routing_context.delegation_depth must be between 0 and 2")
    for key in (
        "caller_is_root", "worker_available", "visible_task_available",
        "context_isolation_required", "cross_project", "long_running", "writer_required",
    ):
        if key in context and type(context[key]) is not bool:
            raise ValueError(f"routing_context.{key} must be boolean")
    for key in (
        "estimated_direct_tokens", "estimated_worker_tokens",
        "estimated_handoff_tokens", "estimated_acceptance_tokens",
    ):
        if key in context and (
            not isinstance(context[key], int)
            or isinstance(context[key], bool)
            or context[key] < 0
        ):
            raise ValueError(f"routing_context.{key} must be a non-negative integer")
    parent = context.get("parent_lease_id")
    if parent is not None and (
        not isinstance(parent, str) or not HEX_ID.fullmatch(parent)
    ):
        raise ValueError("routing_context.parent_lease_id is invalid")
    independent_count = context.get("independent_read_only_count", 1)
    if (
        not isinstance(independent_count, int)
        or isinstance(independent_count, bool)
        or not 1 <= independent_count <= MAX_READ_ONLY_CHILDREN
    ):
        raise ValueError("routing_context.independent_read_only_count must be between 1 and 3")
    return dict(context)


def validate_decision_features(value: Any) -> dict[str, Any]:
    v1_required = {
        "operation_mode", "scope", "spec_state", "reversibility", "cognitive_type",
        "risk_domains", "workload", "user_constraints", "feature_source", "confidence",
    }
    v2_fields = {
        "feature_version", "verification_depth", "evidence_state", "decision_impact", "novelty"
    }
    if not isinstance(value, dict):
        raise TypeError("decision_features must be an object")
    is_v2 = value.get("feature_version") == 2
    required = v1_required | (v2_fields if is_v2 else set())
    allowed_fields = v1_required | v2_fields
    features = _strict_object(value, allowed_fields, required, "decision_features")
    if "feature_version" in features and not is_v2:
        raise ValueError("decision_features.feature_version is invalid")
    for key, allowed in FEATURE_VALUES.items():
        if key not in features:
            continue
        if features[key] not in allowed:
            raise ValueError(f"decision_features.{key} is invalid")
    if not isinstance(features["risk_domains"], list) or any(
        not isinstance(item, str) or item not in KNOWN_RISK_DOMAINS for item in features["risk_domains"]
    ):
        raise ValueError("decision_features.risk_domains is invalid")
    if not isinstance(features["user_constraints"], list) or any(
        item not in {"role", "model", "reasoning_effort", "no_delegation"}
        for item in features["user_constraints"]
    ):
        raise ValueError("decision_features.user_constraints is invalid")
    if features["feature_source"] not in {"structured_heuristic", "caller_supplied", "legacy_v1"}:
        raise ValueError("decision_features.feature_source is invalid")
    if not _is_number(features["confidence"]) or not 0 <= features["confidence"] <= 1:
        raise ValueError("decision_features.confidence is invalid")
    return dict(features)


def _validate_transition(value: Any) -> None:
    transition = _strict_object(
        value, {
            "phase", "role", "model", "reasoning_effort", "stage",
            "execution_target", "delegation_depth", "lease_id", "writer_mode",
        },
        {"phase", "role", "model", "reasoning_effort"}, "transition"
    )
    if transition["phase"] not in {"start", "stop"}:
        raise ValueError("transition.phase is invalid")
    if transition["role"] not in VALID_ROLES | {"default", "worker", "explorer", "unknown"}:
        raise ValueError("transition.role is invalid")
    if transition["model"] not in VALID_MODELS | {"unknown"}:
        raise ValueError("transition.model is invalid")
    if transition["reasoning_effort"] not in VALID_EFFORTS | {"unknown"}:
        raise ValueError("transition.reasoning_effort is invalid")
    if "stage" in transition and transition["stage"] not in STAGE_NAMES | {"unknown"}:
        raise ValueError("transition.stage is invalid")
    if "execution_target" in transition and transition["execution_target"] not in EXECUTION_TARGETS:
        raise ValueError("transition.execution_target is invalid")
    if "delegation_depth" in transition and (
        not isinstance(transition["delegation_depth"], int)
        or isinstance(transition["delegation_depth"], bool)
        or not 0 <= transition["delegation_depth"] <= MAX_DELEGATION_DEPTH
    ):
        raise ValueError("transition.delegation_depth is invalid")
    if "lease_id" in transition and (
        not isinstance(transition["lease_id"], str) or not HEX_ID.fullmatch(transition["lease_id"])
    ):
        raise ValueError("transition.lease_id is invalid")
    if "writer_mode" in transition and transition["writer_mode"] not in WRITER_MODES:
        raise ValueError("transition.writer_mode is invalid")


def _validate_replacement(value: Any) -> None:
    if value is None:
        return
    replacement = _strict_object(
        value, {"role", "model", "reasoning_effort"}, {"role", "model", "reasoning_effort"}, "replacement"
    )
    if replacement["role"] not in VALID_ROLES or replacement["model"] not in VALID_MODELS or replacement["reasoning_effort"] not in VALID_EFFORTS:
        raise ValueError("replacement route is invalid")


def _validate_stage(value: Any, *, v4: bool = False) -> None:
    v4_fields = {
        "execution_target", "execution_mode", "delegation_depth",
        "writer_mode", "access_mode", "lease_required", "stage_id", "attempt",
        "parallelism", "parallel_limit",
    }
    stage = _strict_object(
        value,
        {
            "stage", "authority", "role", "capability_floor", "model",
            "reasoning_effort", "required",
        } | (v4_fields if v4 else set()),
        {
            "stage", "authority", "role", "capability_floor", "model",
            "reasoning_effort", "required",
        } | (v4_fields if v4 else set()),
        "route stage",
    )
    if stage["stage"] not in STAGE_NAMES or stage["authority"] not in AUTHORITIES:
        raise ValueError("route stage classification is invalid")
    if stage["role"] not in VALID_ROLES or stage["model"] not in VALID_MODELS:
        raise ValueError("route stage tuple is invalid")
    if stage["role"] == "direct" and (
        stage["model"] != "gpt-5.6-sol"
        or stage["reasoning_effort"] != "medium"
    ):
        raise ValueError("direct stage must be the Root at Sol Medium")
    expected_floor = AUTHORITY_FLOORS[stage["authority"]]
    if stage["capability_floor"] != expected_floor or MODEL_ORDER[stage["model"]] < MODEL_ORDER[expected_floor]:
        raise ValueError("route stage violates capability floor")
    if stage["reasoning_effort"] not in VALID_EFFORTS or type(stage["required"]) is not bool:
        raise ValueError("route stage effort/required is invalid")
    if v4:
        if stage["execution_target"] not in EXECUTION_TARGETS:
            raise ValueError("route stage execution_target is invalid")
        if stage["execution_mode"] not in EXECUTION_MODES:
            raise ValueError("route stage execution_mode is invalid")
        if (
            not isinstance(stage["delegation_depth"], int)
            or isinstance(stage["delegation_depth"], bool)
            or not 0 <= stage["delegation_depth"] <= MAX_DELEGATION_DEPTH
        ):
            raise ValueError("route stage delegation_depth is invalid")
        if stage["writer_mode"] not in WRITER_MODES or type(stage["lease_required"]) is not bool:
            raise ValueError("route stage writer/lease contract is invalid")
        if stage["access_mode"] not in {"read_only", "writer"}:
            raise ValueError("route stage access_mode is invalid")
        if stage["parallelism"] not in {"serial", "independent_read_only"}:
            raise ValueError("route stage parallelism is invalid")
        if (
            not isinstance(stage["parallel_limit"], int)
            or isinstance(stage["parallel_limit"], bool)
            or not 1 <= stage["parallel_limit"] <= MAX_READ_ONLY_CHILDREN
        ):
            raise ValueError("route stage parallel_limit is invalid")
        if stage["parallelism"] == "independent_read_only" and stage["writer_mode"] != "read_only":
            raise ValueError("only read-only stages may declare independent parallelism")
        if (stage["parallelism"] == "serial") != (stage["parallel_limit"] == 1):
            raise ValueError("route stage parallelism/limit disagree")
        if not isinstance(stage["stage_id"], str) or not HEX_ID.fullmatch(stage["stage_id"]):
            raise ValueError("route stage stage_id is invalid")
        if not isinstance(stage["attempt"], int) or isinstance(stage["attempt"], bool) or stage["attempt"] < 1:
            raise ValueError("route stage attempt is invalid")
        if stage["role"] == "direct" and stage["execution_target"] != "direct":
            raise ValueError("direct role requires direct execution target")


def _validate_token_estimate(value: Any) -> None:
    estimate = _strict_object(
        value,
        {
            "direct_total", "routed_total", "worker", "handoff",
            "acceptance", "selected_total", "selection_reason",
        },
        {
            "direct_total", "routed_total", "worker", "handoff",
            "acceptance", "selected_total", "selection_reason",
        },
        "token_estimate",
    )
    for key in ("direct_total", "routed_total", "worker", "handoff", "acceptance", "selected_total"):
        if not isinstance(estimate[key], int) or isinstance(estimate[key], bool) or estimate[key] < 0:
            raise ValueError(f"token_estimate.{key} is invalid")
    if estimate["selection_reason"] not in {
        "direct_lower_total", "delegated_lower_total", "complex_default",
        "quality_floor", "visible_task_isolation", "dispatch_blocked",
    }:
        raise ValueError("token_estimate.selection_reason is invalid")


def _validate_handoff_contract(value: Any) -> None:
    contract = _strict_object(
        value,
        {"input_contract", "deliverable", "acceptance", "failure_disposition"},
        {"input_contract", "deliverable", "acceptance", "failure_disposition"},
        "handoff_contract",
    )
    if contract != {
        "input_contract": "frozen_scope",
        "deliverable": "bounded_result",
        "acceptance": "root_quality_gate",
        "failure_disposition": "freeze_and_reroute",
    }:
        raise ValueError("handoff_contract is invalid")


def _validate_capability_exception(value: Any) -> None:
    if value is None:
        return
    exception = _strict_object(
        value,
        {"requested_model", "required_floor", "disposition", "decision_owner_model", "reason"},
        {"requested_model", "required_floor", "disposition", "decision_owner_model", "reason"},
        "capability_exception",
    )
    if (
        exception["requested_model"] not in VALID_MODELS
        or exception["required_floor"] not in VALID_MODELS
        or exception["disposition"] != "worker_only"
        or exception["decision_owner_model"] != "gpt-5.6-sol"
        or exception["reason"] != "requested_model_below_capability_floor"
    ):
        raise ValueError("capability_exception is invalid")


def validate_evidence_event(value: Any) -> dict[str, Any]:
    common = {"schema_version", "event_id", "sequence", "created_at", "dedupe_key", "type"}
    route = {
        "task_ref", "task_fingerprint", "route_id", "profile", "task_class", "role", "model",
        "reasoning_effort", "confidence", "decision_features", "constraints", "policy_revision",
        "shadow", "session", "project", "plan_version", "capability_floor", "effort_basis",
        "route_mode", "stages", "capability_exception",
        "execution_target", "execution_mode", "delegation_depth",
        "writer_ownership", "dispatch_ready", "dispatch_blocker",
        "token_estimate", "handoff_contract",
        "visible_task_title",
        "profile_version",
    }
    execution = {"task_ref", "route_id", "event", "tool_kind", "failed", "verification_kind", "transition", "archive_status"}
    outcome = {
        "task_ref", "route_id", "status", "quality_gate", "route_fit", "verification_kinds",
        "confidence", "evidence_source", "objective_verification", "user_confirmed", "replacement",
        "high_risk_regression", "retry_band", "rework_band", "tool_band", "duration_band",
        "token_band", "cost_band", "stage", "model_fit", "effort_fit",
        "context_fit", "tool_data_fit", "failure_axis", "result_signal",
        "stage_source", "audit_followup",
        "dispatch_mode", "observed_execution", "plan_match", "boundary_status",
        "scope_status", "verification_status", "archive_status", "delegation_depth",
        "stage_lease", "local_tokens",
    }
    if not isinstance(value, dict) or value.get("type") not in {"route", "execution", "outcome"}:
        raise ValueError("evidence event type is invalid")
    event_type = value["type"]
    allowed = common | ({"route": route, "execution": execution, "outcome": outcome}[event_type])
    legacy_route = route - {
        "plan_version", "capability_floor", "effort_basis", "route_mode", "stages",
        "capability_exception", "execution_target", "execution_mode",
        "delegation_depth", "writer_ownership", "dispatch_ready",
        "dispatch_blocker", "token_estimate", "handoff_contract",
        "visible_task_title", "profile_version",
    }
    schema_version = value.get("schema_version")
    legacy_outcome = outcome - {
        "stage", "model_fit", "effort_fit", "context_fit", "tool_data_fit", "failure_axis",
        "result_signal", "stage_source", "audit_followup", "dispatch_mode",
        "observed_execution", "plan_match", "boundary_status", "scope_status",
        "verification_status", "archive_status", "delegation_depth", "stage_lease",
        "local_tokens",
    }
    payload_required = {
        "route": route - {"visible_task_title"} if schema_version == 4 else (
            route - {
                "execution_target", "execution_mode", "delegation_depth",
                "writer_ownership", "dispatch_ready", "dispatch_blocker",
                "token_estimate", "handoff_contract",
                "visible_task_title",
                "profile_version",
            } if schema_version == 3 else legacy_route
        ),
        "execution": {"task_ref", "route_id", "event"},
        "outcome": outcome - {"stage", "audit_followup", "local_tokens"} if schema_version == 4 else (
            outcome - {
                "stage", "audit_followup", "dispatch_mode", "observed_execution",
                "plan_match", "boundary_status", "scope_status", "verification_status",
                "archive_status", "delegation_depth", "stage_lease", "local_tokens",
            } if schema_version == 3 else legacy_outcome
        ),
    }[event_type]
    required = common - {"dedupe_key"} | payload_required
    event = _strict_object(value, allowed, required, f"{event_type} event")
    if event["schema_version"] not in {2, 3, 4} or not isinstance(event["sequence"], int) or isinstance(event["sequence"], bool) or event["sequence"] < 1:
        raise ValueError("evidence sequence/schema is invalid")
    try:
        uuid.UUID(str(event["event_id"]))
        uuid.UUID(str(event["route_id"]))
    except (ValueError, AttributeError) as error:
        raise ValueError("evidence UUID is invalid") from error
    if not isinstance(event["created_at"], str) or len(event["created_at"]) > 40:
        raise ValueError("created_at is invalid")
    if "dedupe_key" in event and (not isinstance(event["dedupe_key"], str) or not DEDUPE_ID.fullmatch(event["dedupe_key"])):
        raise ValueError("dedupe_key is invalid")
    for key in ("task_ref",):
        if key in event and (not isinstance(event[key], str) or not HEX_ID.fullmatch(event[key])):
            raise ValueError(f"{key} is invalid")
    if event_type == "route":
        for key in ("task_fingerprint", "session", "project"):
            if not isinstance(event[key], str) or not HEX_ID.fullmatch(event[key]):
                raise ValueError(f"route.{key} is invalid")
        if event["profile"] not in {"generic", "quant"} or event["task_class"] not in FEATURE_VALUES["cognitive_type"]:
            raise ValueError("route classification is invalid")
        if event["role"] not in VALID_ROLES or event["model"] not in VALID_MODELS or event["reasoning_effort"] not in VALID_EFFORTS:
            raise ValueError("route tuple is invalid")
        if "plan_version" in event:
            expected_plan = 3 if event["schema_version"] == 4 else 2
            if event["plan_version"] != expected_plan or event.get("route_mode") not in {"single", "staged"}:
                raise ValueError("route plan version/mode is invalid")
            if event.get("capability_floor") not in VALID_MODELS or MODEL_ORDER[event["model"]] < MODEL_ORDER[event["capability_floor"]]:
                raise ValueError("route violates capability floor")
            if not isinstance(event.get("effort_basis"), list) or any(
                basis not in EFFORT_BASES for basis in event["effort_basis"]
            ):
                raise ValueError("route effort_basis is invalid")
            if not isinstance(event.get("stages"), list) or not event["stages"]:
                raise ValueError("route stages are invalid")
            for stage in event["stages"]:
                _validate_stage(stage, v4=event["schema_version"] == 4)
            _validate_capability_exception(event.get("capability_exception"))
        if event["schema_version"] == 4:
            if event["execution_target"] not in EXECUTION_TARGETS or event["execution_mode"] not in EXECUTION_MODES:
                raise ValueError("route execution target/mode is invalid")
            if event["profile_version"] != 4:
                raise ValueError("route profile version is invalid")
            if not isinstance(event["delegation_depth"], int) or isinstance(event["delegation_depth"], bool) or not 0 <= event["delegation_depth"] <= MAX_DELEGATION_DEPTH:
                raise ValueError("route delegation depth is invalid")
            owner = _strict_object(event["writer_ownership"], {"mode", "owner"}, {"mode", "owner"}, "writer_ownership")
            if owner["mode"] != "single_writer" or owner["owner"] not in VALID_ROLES:
                raise ValueError("writer ownership is invalid")
            if type(event["dispatch_ready"]) is not bool or event["dispatch_blocker"] not in DISPATCH_BLOCKERS:
                raise ValueError("dispatch readiness is invalid")
            if event["dispatch_ready"] != (event["dispatch_blocker"] == "none"):
                raise ValueError("dispatch readiness/blocker disagree")
            _validate_token_estimate(event["token_estimate"])
            _validate_handoff_contract(event["handoff_contract"])
            title = event.get("visible_task_title")
            if title is not None and (
                event["execution_target"] != "visible_task"
                or not isinstance(title, str)
                or not VISIBLE_TASK_TITLE.fullmatch(title)
            ):
                raise ValueError("visible task title is invalid")
        if not _is_number(event["confidence"]) or not 0 <= event["confidence"] <= 1:
            raise ValueError("route confidence is invalid")
        if not isinstance(event["policy_revision"], int) or isinstance(event["policy_revision"], bool) or event["policy_revision"] < 1:
            raise ValueError("policy_revision is invalid")
        validate_decision_features(event["decision_features"])
        if event["schema_version"] in {3, 4} and event["decision_features"].get("feature_version") != 2:
            raise ValueError("route v3/v4 requires Decision Features v2")
        validate_constraints(event["constraints"])
        if event["shadow"] is not None:
            shadow = _strict_object(event["shadow"], {"proposal_id", "axis", "candidate"}, {"proposal_id", "axis", "candidate"}, "shadow")
            if shadow["axis"] not in {"role", "model", "reasoning_effort"} or not isinstance(shadow["candidate"], str) or len(shadow["candidate"]) > 80:
                raise ValueError("shadow is invalid")
    elif event_type == "execution":
        if event["event"] not in HOOK_EVENTS:
            raise ValueError("execution event is invalid")
        if "tool_kind" in event and event["tool_kind"] not in {"shell", "edit", "agent", "mcp", "local", "lifecycle"}:
            raise ValueError("tool_kind is invalid")
        if "failed" in event and type(event["failed"]) is not bool:
            raise ValueError("failed must be boolean")
        if "verification_kind" in event and event["verification_kind"] is not None and event["verification_kind"] not in VERIFICATION_KINDS:
            raise ValueError("verification_kind is invalid")
        if "transition" in event:
            _validate_transition(event["transition"])
        if event["event"] == "ArchiveObserved":
            if (
                event["schema_version"] != 4
                or event.get("archive_status") != "archived"
                or "transition" in event
            ):
                raise ValueError("archive observation is invalid")
        elif "archive_status" in event:
            raise ValueError("archive_status is valid only for ArchiveObserved")
    else:
        if event["status"] not in OUTCOME_STATUSES or event["quality_gate"] not in QUALITY_GATES or event["route_fit"] not in ROUTE_FITS:
            raise ValueError("outcome classification is invalid")
        if not isinstance(event["verification_kinds"], list) or any(item not in VERIFICATION_KINDS for item in event["verification_kinds"]):
            raise ValueError("verification_kinds is invalid")
        if not _is_number(event["confidence"]) or not 0 <= event["confidence"] <= 1:
            raise ValueError("outcome confidence is invalid")
        if event["evidence_source"] not in {"objective", "hook_heuristic", "user_explicit", "legacy_v1"}:
            raise ValueError("evidence_source is invalid")
        for key in ("objective_verification", "user_confirmed", "high_risk_regression"):
            if type(event[key]) is not bool:
                raise ValueError(f"{key} must be boolean")
        _validate_replacement(event["replacement"])
        if any(event[key] not in BANDS for key in ("retry_band", "rework_band", "tool_band", "duration_band", "token_band", "cost_band")):
            raise ValueError("outcome resource band is invalid")
        if event["schema_version"] in {3, 4}:
            if "stage" in event and event["stage"] not in STAGE_NAMES:
                raise ValueError("outcome stage is invalid")
            if event["model_fit"] not in MODEL_EFFORT_FITS or event["effort_fit"] not in MODEL_EFFORT_FITS:
                raise ValueError("outcome model/effort fit is invalid")
            if event["context_fit"] not in CONTEXT_TOOL_FITS or event["tool_data_fit"] not in CONTEXT_TOOL_FITS:
                raise ValueError("outcome context/tool-data fit is invalid")
            if event["failure_axis"] not in FAILURE_AXES:
                raise ValueError("outcome failure_axis is invalid")
            if event["result_signal"] not in RESULT_SIGNALS:
                raise ValueError("outcome result_signal is invalid")
            if event["stage_source"] not in STAGE_SOURCES:
                raise ValueError("outcome stage_source is invalid")
            if event["stage_source"] != "unknown" and "stage" not in event:
                raise ValueError("outcome stage_source requires a stage")
            if "audit_followup" in event:
                _validate_stage(event["audit_followup"], v4=event["schema_version"] == 4)
                if (
                    event["result_signal"] != "exceptional_positive"
                    or event["audit_followup"]["stage"] != "audit"
                    or event["audit_followup"]["authority"] != "audit"
                ):
                    raise ValueError("outcome audit_followup is invalid")
        if event["schema_version"] == 4:
            if event["dispatch_mode"] not in EXECUTION_TARGETS:
                raise ValueError("outcome dispatch_mode is invalid")
            observed = _strict_object(
                event["observed_execution"],
                {"role", "model", "reasoning_effort", "execution_target"},
                {"role", "model", "reasoning_effort", "execution_target"},
                "observed_execution",
            )
            if (
                observed["role"] not in VALID_ROLES | {"unknown"}
                or observed["model"] not in VALID_MODELS | {"unknown"}
                or observed["reasoning_effort"] not in VALID_EFFORTS | {"unknown"}
                or observed["execution_target"] not in EXECUTION_TARGETS | {"unknown"}
            ):
                raise ValueError("observed execution provenance is invalid")
            if event["plan_match"] not in PLAN_MATCHES or event["boundary_status"] not in BOUNDARY_STATUSES:
                raise ValueError("outcome plan/boundary state is invalid")
            if event["scope_status"] not in SCOPE_STATUSES or event["verification_status"] not in VERIFICATION_STATUSES:
                raise ValueError("outcome scope/verification state is invalid")
            if event["archive_status"] not in ARCHIVE_STATUSES:
                raise ValueError("outcome archive state is invalid")
            if not isinstance(event["delegation_depth"], int) or isinstance(event["delegation_depth"], bool) or not 0 <= event["delegation_depth"] <= MAX_DELEGATION_DEPTH:
                raise ValueError("outcome delegation depth is invalid")
            lease = _strict_object(event["stage_lease"], {"lease_id", "status"}, {"lease_id", "status"}, "stage_lease")
            if lease["lease_id"] != "unknown" and (not isinstance(lease["lease_id"], str) or not HEX_ID.fullmatch(lease["lease_id"])):
                raise ValueError("stage lease id is invalid")
            if lease["status"] not in LEASE_STATUSES | {"unknown"}:
                raise ValueError("stage lease status is invalid")
            if "local_tokens" in event:
                local = _strict_object(event["local_tokens"], {"input", "output", "total", "source", "complete"}, {"input", "output", "total", "source", "complete"}, "local_tokens")
                if any(not isinstance(local[k], int) or isinstance(local[k], bool) or local[k] < 0 for k in ("input", "output", "total")) or local["total"] != local["input"] + local["output"]:
                    raise ValueError("local token counts are invalid")
                if local["source"] not in {"provider_usage", "codex_usage", "caller_supplied"} or type(local["complete"]) is not bool:
                    raise ValueError("local token provenance is invalid")
    return event


def project_public_evidence_event(value: dict[str, Any]) -> dict[str, Any]:
    """Project local v4 evidence to the enum/HMAC/band-only GitHub contract."""
    event = json.loads(json.dumps(value))
    if event.get("schema_version") != 4:
        return event
    event.pop("local_tokens", None)
    event.pop("visible_task_title", None)
    event.pop("token_estimate", None)
    return event


def validate_public_evidence_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("public evidence event must be an object")
    if value.get("schema_version") != 4:
        return validate_evidence_event(value)
    candidate = dict(value)
    if candidate.get("type") == "route":
        candidate.setdefault(
            "token_estimate",
            {
                "direct_total": 0,
                "routed_total": 0,
                "worker": 0,
                "handoff": 0,
                "acceptance": 0,
                "selected_total": 0,
                "selection_reason": "quality_floor",
            },
        )
        candidate.setdefault("visible_task_title", None)
    candidate.pop("local_tokens", None)
    return validate_evidence_event(candidate)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def plugin_data_root() -> Path:
    explicit = os.environ.get("CODEX_ADAPTIVE_ROUTER_DATA")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return _codex_home() / "codex-adaptive-router"


def _codex_home() -> Path:
    return Path(
        os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser().resolve()


def legacy_plugin_data_roots(canonical_root: Path | None = None) -> list[Path]:
    canonical = (canonical_root or plugin_data_root()).resolve(strict=False)
    candidates: set[Path] = set()
    supplied = os.environ.get("PLUGIN_DATA")
    if supplied:
        candidates.add(Path(supplied).expanduser().resolve(strict=False))
    legacy_parent = _codex_home() / "plugins" / "data"
    if legacy_parent.is_dir():
        candidates.update(
            path.resolve(strict=False)
            for path in legacy_parent.glob("codex-adaptive-router-*")
            if path.is_dir()
        )
    return sorted(
        (path for path in candidates if path != canonical and path.is_dir()),
        key=lambda path: os.path.normcase(str(path)),
    )


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    result.append(value)
    except OSError:
        pass
    return result


def _read_jsonl_strict(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ValueError(f"unable to read {label} event log") from error
    result: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"invalid {label} event log at line {number}"
            ) from error
        if not isinstance(value, dict):
            raise TypeError(f"invalid {label} event log at line {number}")
        result.append(value)
    return result


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            # ERROR_INVALID_PARAMETER is the documented result for a PID that
            # does not exist. Access denied and unknown failures are treated as
            # alive so stale-lock recovery cannot delete a live owner's lock.
            return ctypes.get_last_error() != error_invalid_parameter
        try:
            result = kernel32.WaitForSingleObject(handle, 0)
            if result == wait_object_0:
                return False
            if result == wait_timeout:
                return True
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        return error.errno not in {errno.ESRCH, errno.EINVAL}
    return True


_IN_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_IN_PROCESS_LOCKS_GUARD = threading.Lock()


def _in_process_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _IN_PROCESS_LOCKS_GUARD:
        return _IN_PROCESS_LOCKS.setdefault(key, threading.RLock())


def _unlink_lock_if_owned(
    lock: Path, expected_token: Any, *, attempts: int = 6
) -> bool:
    """Remove only the lock generation observed by the caller.

    Windows can transiently reject unlink while another handle is closing.  The
    owner token is re-read before every retry so a replacement lock is never
    removed by an earlier owner.
    """
    for attempt in range(attempts):
        current = _read_json(lock, {})
        current_token = current.get("token") if isinstance(current, dict) else None
        if current_token != expected_token:
            return False
        try:
            lock.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError as error:
            if getattr(error, "winerror", None) != 32 or attempt + 1 >= attempts:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.16))
    return False


@contextlib.contextmanager
def _file_lock(
    path: Path, timeout_seconds: float = 3.0, stale_seconds: float = 30.0
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    with _in_process_lock(lock):
        deadline = time.monotonic() + timeout_seconds
        owner = {
            "pid": os.getpid(),
            "token": str(uuid.uuid4()),
            "created": time.time(),
        }
        descriptor = None
        while descriptor is None:
            try:
                descriptor = os.open(
                    str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(descriptor, json.dumps(owner).encode("ascii"))
                os.fsync(descriptor)
            except FileExistsError:
                existing = _read_json(lock, {})
                pid = existing.get("pid") if isinstance(existing, dict) else None
                token = existing.get("token") if isinstance(existing, dict) else None
                created = (
                    existing.get("created") if isinstance(existing, dict) else None
                )
                if isinstance(created, (int, float)):
                    age = time.time() - float(created)
                else:
                    with contextlib.suppress(OSError):
                        created = lock.stat().st_mtime
                    age = (
                        time.time() - float(created)
                        if isinstance(created, (int, float))
                        else None
                    )
                stale = (
                    isinstance(pid, int)
                    and not _process_alive(pid)
                    or (pid is None and age is not None and age >= stale_seconds)
                )
                if stale:
                    _unlink_lock_if_owned(lock, token)
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"router storage is busy: {path.name}")
                time.sleep(0.02)
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                    descriptor = None
                    with contextlib.suppress(OSError):
                        _unlink_lock_if_owned(lock, owner["token"])
                raise
        try:
            yield
        finally:
            os.close(descriptor)
            _unlink_lock_if_owned(lock, owner["token"])


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _atomic_write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for value in values:
                handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _append_unlocked(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _profile_path(name: str) -> Path:
    return PLUGIN_ROOT / "profiles" / f"{name}.json"


def available_profiles() -> list[str]:
    return sorted(x.stem for x in (PLUGIN_ROOT / "profiles").glob("*.json"))


def load_profile(name: str) -> dict[str, Any]:
    selected = name if _profile_path(name).is_file() else "generic"
    value = _read_json(_profile_path(selected), {})
    if not isinstance(value, dict) or not isinstance(value.get("roles"), dict):
        raise TypeError(f"invalid router profile: {selected}")
    return value


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "revision": 1,
        "updated_at": utc_now(),
        "learning": {
            "minimum_replacement_outcomes": 5,
            "minimum_independent_sessions": 3,
            "minimum_distinct_projects_for_global": 2,
            "minimum_confidence": 0.85,
            "shadow_minimum_comparable": 10,
            "shadow_minimum_support": 8,
            "shadow_maximum_losses": 1,
        },
        "overrides": [],
    }


def policy_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "policy" / "current.json"


def events_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "events" / "routing.jsonl"


def ledger_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "state" / "task-ledger.json"


def salt_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "private" / "identity.salt"


def shadows_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "learning" / "shadows.json"


def state_lock_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "state" / "router-state"


def load_policy(root: Path | None = None) -> dict[str, Any]:
    value = _read_json(policy_path(root), None)
    if not isinstance(value, dict):
        return default_policy()
    if value.get("schema_version") == 1:
        migrated = default_policy()
        migrated["revision"] = int(value.get("revision") or 1)
        migrated["overrides"] = list(value.get("overrides") or [])
        return migrated
    if value.get("schema_version") != 2:
        return default_policy()
    defaults = default_policy()
    value.setdefault("overrides", [])
    value.setdefault("learning", {})
    for key, item in defaults["learning"].items():
        value["learning"].setdefault(key, item)
    return value


def save_policy(policy: dict[str, Any], root: Path | None = None) -> None:
    policy["schema_version"] = 2
    policy["updated_at"] = utc_now()
    with _file_lock(state_lock_path(root)):
        _atomic_write_json(policy_path(root), policy)


def _salt(root: Path | None = None) -> bytes:
    path = salt_path(root)
    try:
        raw = path.read_bytes()
        if len(raw) >= 32:
            return raw
    except OSError:
        pass
    candidate = secrets.token_bytes(32)
    with _file_lock(path):
        try:
            raw = path.read_bytes()
            if len(raw) >= 32:
                return raw
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            fd = os.open(
                str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(candidate)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        raw = path.read_bytes()
        if len(raw) < 32:
            raise RuntimeError("router identity salt publication was incomplete")
        return raw


def identity(value: str, root: Path | None = None) -> str:
    return hmac.new(
        _salt(root), value.encode("utf-8", errors="replace"), hashlib.sha256
    ).hexdigest()[:32]


def fingerprint(value: str) -> str:  # v1 compatibility only
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]


def load_shadows(root: Path | None = None) -> dict[str, Any]:
    value = _read_json(shadows_path(root), {"schema_version": 2, "items": {}})
    if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
        return {"schema_version": 2, "items": {}}
    value["schema_version"] = 2
    return value


def save_shadows(value: dict[str, Any], root: Path | None = None) -> None:
    value["schema_version"] = 2
    with _file_lock(state_lock_path(root)):
        _atomic_write_json(shadows_path(root), value)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


QUANT_TERMS = (
    "quant",
    "strategy",
    "backtest",
    "sharpe",
    "factor",
    "回测",
    "策略",
    "量化",
    "夏普",
    "因子",
)
MAPPER_TERMS = (
    "find ",
    "locate",
    "where is",
    "search code",
    "call chain",
    "reference",
    "定位",
    "调用链",
    "搜索代码",
)
RUNNER_TERMS = (
    "batch",
    "parameter sweep",
    "run tests",
    "benchmark",
    "collect metrics",
    "批量",
    "参数扫描",
    "跑测试",
    "收集指标",
)
IMPLEMENTATION_TERMS = (
    "implement",
    "add ",
    "build ",
    "refactor",
    "fix ",
    "实现",
    "新增",
    "重构",
    "修复",
)
ARCHITECTURE_TERMS = (
    "architecture",
    "semantic",
    "data model",
    "system design",
    "irreversible",
    "架构",
    "语义",
    "数据模型",
    "不可逆",
)
RESEARCH_TERMS = (
    "why",
    "root cause",
    "diagnose",
    "hypothesis",
    "statistical",
    "research",
    "原因",
    "根因",
    "诊断",
    "研究",
)
AUDIT_TERMS = (
    "audit",
    "credible",
    "too good",
    "leakage",
    "overfit",
    "adversarial",
    "审计",
    "异常好",
    "数据泄漏",
    "过拟合",
)
SCOUT_TERMS = (
    "new direction",
    "novel",
    "brainstorm",
    "local optimum",
    "新方向",
    "发散",
    "局部最优",
)
SIMPLE_TERMS = (
    "translate",
    "rewrite",
    "rename",
    "explain this line",
    "翻译",
    "改写",
    "一句",
    "命名",
)
CORRECTION_TERMS = (
    "wrong",
    "incorrect",
    "redo",
    "you missed",
    "not what i",
    "不对",
    "错了",
    "重做",
    "遗漏",
    "不是我要",
)
OVERRIDE_TERMS = ("use instead", "switch to", "override", "改用", "换成", "指定")
EXCEPTIONAL_TERMS = (
    "exceptional",
    "anomalous",
    "unusually good",
    "best ever",
    "异常优秀",
    "异常强",
)


def infer_profile(task: str, requested: str | None = None) -> str:
    if requested in available_profiles():
        return str(requested)
    return "quant" if _contains(_normalise(task), QUANT_TERMS) else "generic"


def infer_decision_features(
    task: str, *, task_state: str = "unknown", supplied: dict[str, Any] | None = None
) -> dict[str, Any]:
    text = _normalise(task)
    features = {
        "feature_version": 2,
        "operation_mode": "answer",
        "scope": (
            "tiny"
            if len(text) < 100
            else "bounded" if len(text) < 500 else "multi_file"
        ),
        "spec_state": "frozen" if task_state == "frozen" else "unknown",
        "reversibility": "reversible",
        "cognitive_type": "direct",
        "risk_domains": [],
        "workload": "small" if len(text) < 180 else "medium",
        "verification_depth": "standard",
        "evidence_state": "unknown",
        "decision_impact": "low",
        "novelty": "routine",
        "user_constraints": [],
        "feature_source": "structured_heuristic",
        "confidence": 0.72,
    }
    if _contains(text, ARCHITECTURE_TERMS):
        features.update(
            operation_mode="research",
            cognitive_type="architecture",
            reversibility="costly",
            confidence=0.9,
        )
    elif _contains(text, AUDIT_TERMS):
        features.update(
            operation_mode="review", cognitive_type="audit", confidence=0.92
        )
    elif _contains(text, RESEARCH_TERMS):
        features.update(
            operation_mode="diagnose", cognitive_type="diagnosis", confidence=0.86
        )
    elif _contains(text, MAPPER_TERMS):
        features.update(
            operation_mode="research", cognitive_type="discovery", confidence=0.88
        )
    elif _contains(text, RUNNER_TERMS):
        features.update(
            operation_mode="execute",
            cognitive_type="execution",
            workload="batch",
            confidence=0.9,
        )
    elif _contains(text, IMPLEMENTATION_TERMS):
        features.update(
            operation_mode="change", cognitive_type="implementation", confidence=0.84
        )
    elif _contains(text, SCOUT_TERMS):
        features.update(
            operation_mode="research", cognitive_type="exploration", confidence=0.9
        )
    if _contains(text, QUANT_TERMS):
        features["risk_domains"].append("quantitative_research")
    if _contains(
        text,
        ("production", "security", "privacy", "deploy", "生产", "安全", "隐私", "部署"),
    ):
        features["risk_domains"].append("high_impact")
        features["decision_impact"] = "high"
    if features["scope"] in {"multi_file", "cross_system"}:
        features["decision_impact"] = "medium"
    if features["cognitive_type"] in {"architecture", "audit"}:
        features["verification_depth"] = "deep"
        features["decision_impact"] = "high"
    if features["cognitive_type"] == "exploration":
        features["novelty"] = "open_ended"
    if supplied:
        allowed = set(features)
        for key, value in supplied.items():
            if key not in allowed:
                raise ValueError(f"unknown decision feature: {key}")
            if key in FEATURE_VALUES and value not in FEATURE_VALUES[key]:
                raise ValueError(f"invalid decision feature {key}: {value}")
            if key == "feature_version" and value != 2:
                raise ValueError("invalid decision feature feature_version")
            if key == "risk_domains" and (
                not isinstance(value, list)
                or any(x not in KNOWN_RISK_DOMAINS for x in value)
            ):
                raise ValueError("risk_domains must contain known values")
            if key == "user_constraints" and (
                not isinstance(value, list)
                or any(
                    x not in {"role", "model", "reasoning_effort", "no_delegation"}
                    for x in value
                )
            ):
                raise ValueError("user_constraints must contain structured names")
            features[key] = value
        if not 0 <= float(features["confidence"]) <= 1:
            raise ValueError("feature confidence must be between 0 and 1")
        features["feature_source"] = "caller_supplied"
    return validate_decision_features(features)


ROLE_BY_COGNITIVE = {
    "direct": "direct",
    "discovery": "router_code_mapper",
    "execution": "router_experiment_runner",
    "implementation": "router_research_engineer",
    "diagnosis": "router_researcher",
    "research": "router_researcher",
    "architecture": "router_architect",
    "audit": "router_adversarial_auditor",
    "exploration": "router_strategy_scout",
}


@dataclass(frozen=True)
class RoutePlan:
    route_id: str
    profile: str
    task_class: str
    role: str
    model: str
    reasoning_effort: str
    confidence: float
    reasons: list[str]
    escalation_triggers: list[str]
    output_contract: str
    decision_features: dict[str, Any]
    constraints: dict[str, Any]
    plan_version: int
    profile_version: int
    capability_floor: str
    effort_basis: list[str]
    route_mode: str
    stages: list[dict[str, Any]]
    capability_exception: dict[str, Any] | None
    execution_target: str
    execution_mode: str
    delegation_depth: int
    writer_ownership: dict[str, str]
    dispatch_ready: bool
    dispatch_blocker: str
    token_estimate: dict[str, int | str]
    handoff_contract: dict[str, str]
    visible_task_title: str | None
    shadow_recommendation: dict[str, Any] | None = None


def _active_overrides(
    policy: dict[str, Any], profile: str, task_class: str,
    fixed_context: dict[str, Any] | None = None,
) -> dict[str, str]:
    result = {}
    for item in policy.get("overrides", []):
        if item.get("profile") != profile or item.get("task_class") != task_class:
            continue
        fixed = item.get("fixed")
        if isinstance(fixed, dict) and (
            fixed_context is None
            or any(str(fixed_context.get(key)) != str(value) for key, value in fixed.items())
        ):
            continue
        if item.get("axis") in {"role", "model", "reasoning_effort"}:
            result[item["axis"]] = str(item.get("to"))
        elif isinstance(item.get("to"), dict):
            result.update(
                {
                    k: str(v)
                    for k, v in item["to"].items()
                    if k in {"role", "model", "reasoning_effort"}
                }
            )
    return result


def _shadow_recommendation(
    profile: str, task_class: str, root: Path | None,
    fixed_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for proposal_id, item in load_shadows(root)["items"].items():
        proposal = item.get("proposal", {})
        if (
            item.get("state") == "active"
            and proposal.get("profile") == profile
            and proposal.get("task_class") == task_class
            and (
                not isinstance(proposal.get("fixed"), dict)
                or fixed_context is not None
                and all(
                    str(fixed_context.get(key)) == str(value)
                    for key, value in proposal["fixed"].items()
                )
            )
        ):
            return {
                "proposal_id": proposal_id,
                "axis": proposal.get("axis"),
                "candidate": proposal.get("to"),
            }
    return None


def _role_config(profile: dict[str, Any], role: str) -> dict[str, Any]:
    config = profile["roles"].get(role)
    if not isinstance(config, dict):
        raise TypeError(f"profile has no role named {role}")
    return config


def _validate_route_tuple(
    profile: dict[str, Any], role: str, model: str, effort: str, *, explicit_effort: bool = False
) -> dict[str, Any]:
    config = _role_config(profile, role)
    allowed_models = list(
        config.get("allowed_models") or [config.get("default_model") or config.get("model")]
    )
    authority = str(config.get("authority") or "decision")
    floor = str(config.get("capability_floor") or AUTHORITY_FLOORS[authority])
    if floor != AUTHORITY_FLOORS.get(authority) or MODEL_ORDER.get(model, 0) < MODEL_ORDER.get(floor, 99):
        raise ValueError(f"model {model} is below capability floor {floor} for role {role}")
    if model not in VALID_MODELS or model not in allowed_models:
        raise ValueError(f"model {model} is not allowed for role {role}")
    if effort not in VALID_EFFORTS:
        raise ValueError("unsupported reasoning effort")
    effort_config = config.get("effort")
    if isinstance(effort_config, dict):
        order = ["low", "medium", "high", "xhigh", "max", "ultra"]
        minimum = str(effort_config.get("min"))
        maximum = str(effort_config.get("max"))
        explicit_extended_effort = explicit_effort and effort in {"max", "ultra"}
        if minimum not in order or maximum not in order or (
            not explicit_extended_effort
            and not order.index(minimum) <= order.index(effort) <= order.index(maximum)
        ):
            raise ValueError(f"effort {effort} is outside role {role} bounds")
    if effort in {"max", "ultra"} and not explicit_effort:
        raise ValueError("Max/Ultra require an explicit user constraint")
    return config


def _clamp_effort(effort: str, config: dict[str, Any]) -> tuple[str, bool]:
    bounds = config["effort"]
    minimum = str(bounds["min"])
    maximum = str(bounds["max"])
    rank = min(max(EFFORT_ORDER[effort], EFFORT_ORDER[minimum]), EFFORT_ORDER[maximum])
    selected = next(name for name, value in EFFORT_ORDER.items() if value == rank)
    return selected, selected != effort


def _deterministic_effort(
    features: dict[str, Any], config: dict[str, Any], explicit: str | None = None
) -> tuple[str, list[str]]:
    if explicit is not None:
        effort, clamped = _clamp_effort(explicit, config)
        basis = ["explicit_constraint"]
        if clamped:
            basis.append("role_clamp")
        return effort, basis

    candidates: list[tuple[str, str]] = [("medium", "default")]
    if (
        features["cognitive_type"] == "direct"
        and features["scope"] == "tiny"
        and features["reversibility"] == "reversible"
        and features["evidence_state"] != "conflicting"
        and features["verification_depth"] == "basic"
    ):
        candidates.append(("low", "mechanical_basic"))
    if features["scope"] in {"multi_file", "cross_system"}:
        candidates.append(("high", "broad_scope"))
    if features["cognitive_type"] == "implementation" and features["spec_state"] == "frozen":
        candidates.append(("high", "frozen_implementation"))
    if features["verification_depth"] == "deep":
        candidates.append(("high", "deep_verification"))
    if features["evidence_state"] == "conflicting":
        candidates.append(("high", "conflicting_evidence"))
    if features["decision_impact"] == "high" or features["reversibility"] == "costly":
        candidates.append(("high", "costly_impact"))
    if features["cognitive_type"] == "audit" or features["verification_depth"] == "adversarial":
        candidates.append(("xhigh", "audit"))
    if features["novelty"] == "open_ended":
        candidates.append(("xhigh", "open_ended"))
    if features["reversibility"] == "irreversible" or features["decision_impact"] == "critical":
        candidates.append(("xhigh", "irreversible_or_critical"))
    highest = max(EFFORT_ORDER[effort] for effort, _ in candidates)
    selected = next(effort for effort, rank in EFFORT_ORDER.items() if rank == highest)
    basis = [basis for effort, basis in candidates if EFFORT_ORDER[effort] == highest]
    selected, clamped = _clamp_effort(selected, config)
    if clamped:
        basis.append("role_clamp")
    return selected, basis


def _stage(
    loaded: dict[str, Any], stage: str, authority: str, role: str, effort: str,
    *, delegation_depth: int = 0, execution_target: str | None = None,
    writer_mode: str = "read_only",
) -> dict[str, Any]:
    config = _role_config(loaded, role)
    floor = str(config["capability_floor"])
    if authority != config["authority"]:
        raise ValueError(f"role {role} cannot hold {authority} authority")
    model = str(config["default_model"])
    effort, _ = _clamp_effort(effort, config)
    _validate_route_tuple(
        loaded, role, model, effort, explicit_effort=effort in {"max", "ultra"}
    )
    target = execution_target or ("direct" if role == "direct" else "subagent")
    return {
        "stage": stage,
        "authority": authority,
        "role": role,
        "capability_floor": floor,
        "model": model,
        "reasoning_effort": effort,
        "required": True,
        "execution_target": target,
        "execution_mode": (
            "root" if target == "direct" else "isolated" if target == "visible_task" else "delegated"
        ),
        "delegation_depth": min(
            MAX_DELEGATION_DEPTH,
            delegation_depth + (0 if target == "direct" else 1),
        ),
        "writer_mode": writer_mode,
        "access_mode": "writer" if writer_mode == "single_writer" else "read_only",
        "parallelism": "serial",
        "parallel_limit": 1,
        "lease_required": target != "direct",
        "stage_id": uuid.uuid4().hex,
        "attempt": 1,
    }


def _route_stages(
    loaded: dict[str, Any], task_class: str, effort: str, *, add_audit: bool,
    needs_implementation: bool = False, delegation_depth: int = 0,
    worker_target: str = "subagent",
    independent_read_only_count: int = 1,
) -> list[dict[str, Any]]:
    if task_class == "direct":
        stages = [_stage(loaded, "synthesize", "decision", "direct", effort, delegation_depth=delegation_depth)]
    elif task_class in {"discovery", "execution"}:
        worker = "router_code_mapper" if task_class == "discovery" else "router_experiment_runner"
        stages = [
            _stage(loaded, "collect", "evidence", worker, effort, delegation_depth=delegation_depth, execution_target=worker_target),
            _stage(loaded, "synthesize", "decision", "direct", "medium", delegation_depth=delegation_depth),
        ]
    elif task_class == "implementation":
        stages = [
            _stage(loaded, "frame", "decision", "direct", "medium", delegation_depth=delegation_depth),
            _stage(loaded, "implement", "implementation", "router_research_engineer", effort, delegation_depth=delegation_depth, execution_target=worker_target, writer_mode="single_writer"),
            _stage(loaded, "verify", "evidence", "router_experiment_runner", "medium", delegation_depth=delegation_depth, execution_target=worker_target),
            _stage(loaded, "synthesize", "decision", "direct", "medium", delegation_depth=delegation_depth),
        ]
    elif task_class in {"research", "diagnosis", "architecture", "exploration", "audit"}:
        decision_role = {
            "architecture": "router_architect",
            "exploration": "router_strategy_scout",
        }.get(task_class, "router_quant_researcher" if loaded.get("name") == "quant" else "router_researcher")
        stages = [
            _stage(loaded, "frame", "decision", decision_role, effort, delegation_depth=delegation_depth, execution_target=worker_target),
            _stage(loaded, "collect", "evidence", "router_code_mapper", "medium", delegation_depth=delegation_depth, execution_target=worker_target),
        ]
        if needs_implementation:
            stages.append(
                _stage(loaded, "implement", "implementation", "router_research_engineer", "high", delegation_depth=delegation_depth, execution_target=worker_target, writer_mode="single_writer")
            )
        stages.append(
            _stage(loaded, "synthesize", "decision", decision_role, effort, delegation_depth=delegation_depth, execution_target=worker_target)
        )
    else:
        stages = [_stage(loaded, "synthesize", "decision", "direct", "medium", delegation_depth=delegation_depth)]
    if add_audit and stages[-1]["stage"] != "audit":
        stages.append(
            _stage(loaded, "audit", "audit", "router_adversarial_auditor", "xhigh", delegation_depth=delegation_depth, execution_target=worker_target)
        )
    if independent_read_only_count > 1:
        parallel_stage = next(
            (
                item for item in stages
                if item["authority"] == "evidence"
                and item["writer_mode"] == "read_only"
                and item["execution_target"] != "direct"
            ),
            None,
        )
        if parallel_stage is None:
            raise ValueError("route has no read-only evidence stage for declared parallel work")
        parallel_stage["parallelism"] = "independent_read_only"
        parallel_stage["parallel_limit"] = independent_read_only_count
    return stages


def _token_estimate(
    features: dict[str, Any], context: dict[str, Any], *, prefer_direct: bool,
    quality_requires_specialist: bool, visible_task: bool, blocked: bool,
) -> dict[str, int | str]:
    defaults = {"small": 1600, "medium": 4200, "large": 9000, "batch": 14000}
    direct = int(context.get("estimated_direct_tokens", defaults[features["workload"]]))
    worker = int(context.get("estimated_worker_tokens", max(700, round(direct * 0.58))))
    handoff = int(context.get("estimated_handoff_tokens", 650))
    acceptance = int(context.get("estimated_acceptance_tokens", 500))
    routed = worker + handoff + acceptance
    if blocked:
        reason = "dispatch_blocked"
    elif quality_requires_specialist:
        reason = "quality_floor"
    elif visible_task:
        reason = "visible_task_isolation"
    elif prefer_direct:
        reason = "direct_lower_total"
    elif routed < direct:
        reason = "delegated_lower_total"
    else:
        reason = "complex_default"
    return {
        "direct_total": direct,
        "routed_total": routed,
        "worker": worker,
        "handoff": handoff,
        "acceptance": acceptance,
        "selected_total": direct if prefer_direct else routed,
        "selection_reason": reason,
    }


def _visible_task_title(model: str, effort: str, task_class: str) -> str:
    model_name = model.rsplit("-", 1)[-1].upper()
    objective = {
        "implementation": "实现冻结规格",
        "architecture": "冻结架构语义",
        "research": "完成研究判断",
        "diagnosis": "完成根因诊断",
        "exploration": "探索新方向",
        "audit": "执行对抗审计",
    }.get(task_class, "完成隔离任务")
    return f"[AR][{model_name}-{effort.upper()}] {objective}"


def make_route_plan(
    task: str,
    *,
    profile: str | None = None,
    task_state: str = "unknown",
    force_role: str | None = None,
    decision_features: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    routing_context: dict[str, Any] | None = None,
    route_id: str | None = None,
    root: Path | None = None,
) -> RoutePlan:
    if not task.strip():
        raise ValueError("task must not be empty")
    if task_state not in TASK_STATES:
        raise ValueError("invalid task_state")
    selected = infer_profile(task, profile)
    constraints = validate_constraints(constraints)
    routing_context = validate_routing_context(routing_context)
    features = infer_decision_features(
        task, task_state=task_state, supplied=decision_features
    )
    features["user_constraints"] = sorted(constraints)
    task_class = str(features["cognitive_type"])
    original_task_class = task_class
    if task_class == "implementation" and features["spec_state"] != "frozen":
        task_class = "research"
    role = force_role or ROLE_BY_COGNITIVE.get(task_class, "direct")
    loaded = load_profile(selected)
    if (
        selected == "quant"
        and task_class in {"research", "diagnosis"}
        and force_role is None
    ):
        role = "router_quant_researcher"
    token_policy = dict(loaded.get("token_policy") or {})
    token_context = dict(routing_context)
    token_context.setdefault("estimated_handoff_tokens", int(token_policy.get("handoff_tokens", 650)))
    token_context.setdefault("estimated_acceptance_tokens", int(token_policy.get("acceptance_tokens", 500)))
    override = _active_overrides(load_policy(root), selected, task_class)
    if not force_role and "role" not in constraints and not constraints.get("no_delegation"):
        role = override.get("role", role)
    if constraints.get("no_delegation"):
        role = "direct"
        task_class = "direct"
    elif constraints.get("role"):
        role = str(constraints["role"])

    config = _role_config(loaded, role)
    floor = str(config["capability_floor"])
    default_model = str(config["default_model"])
    requested_model = constraints.get("model", override.get("model"))
    capability_exception = None
    model = default_model
    if requested_model:
        requested_model = str(requested_model)
        if MODEL_ORDER[requested_model] < MODEL_ORDER[floor]:
            capability_exception = {
                "requested_model": requested_model,
                "required_floor": floor,
                "disposition": "worker_only",
                "decision_owner_model": "gpt-5.6-sol",
                "reason": "requested_model_below_capability_floor",
            }
        else:
            model = requested_model

    explicit_effort = constraints.get("reasoning_effort")
    policy_effort = override.get("reasoning_effort") if explicit_effort is None else None
    effort, effort_basis = _deterministic_effort(
        features,
        config,
        str(explicit_effort or policy_effort) if (explicit_effort or policy_effort) else None,
    )
    if policy_effort and "explicit_constraint" in effort_basis:
        effort_basis[effort_basis.index("explicit_constraint")] = "policy_override"
    _validate_route_tuple(
        loaded,
        role,
        model,
        effort,
        explicit_effort=bool(explicit_effort or policy_effort),
    )

    delegation_depth = int(routing_context.get("delegation_depth", 0))
    caller_is_root = bool(routing_context.get("caller_is_root", delegation_depth == 0))
    isolation = bool(
        routing_context.get("context_isolation_required")
        or routing_context.get("cross_project")
        or routing_context.get("long_running")
    )
    quality_requires_specialist = (
        effort != "medium"
        or (
            config["authority"] in {"decision", "audit"}
            and role != "direct"
        )
    )
    no_delegation_quality_block = bool(
        (
            constraints.get("no_delegation")
            or constraints.get("role") == "direct"
            or force_role == "direct"
        )
        and original_task_class in {
            "implementation", "diagnosis", "research", "architecture", "audit", "exploration"
        }
    )
    rough_direct = int(token_context.get("estimated_direct_tokens", {
        "small": 1600, "medium": 4200, "large": 9000, "batch": 14000,
    }[features["workload"]]))
    rough_routed = (
        int(token_context.get("estimated_worker_tokens", max(700, round(rough_direct * 0.58))))
        + int(token_context.get("estimated_handoff_tokens", 650))
        + int(token_context.get("estimated_acceptance_tokens", 500))
    )
    direct_exception = (
        task_class != "direct"
        and not quality_requires_specialist
        and not isolation
        and rough_routed - rough_direct >= int(token_policy.get("minimum_direct_savings_tokens", 400))
        and (rough_routed - rough_direct) / max(rough_routed, 1) >= float(token_policy.get("minimum_direct_savings_ratio", 0.15))
        and features["verification_depth"] in {"basic", "standard"}
        and features["decision_impact"] in {"low", "medium"}
        and features["reversibility"] != "irreversible"
        and not set(features["risk_domains"]) & {"quantitative_research", "high_impact"}
    )
    execution_target = (
        "direct" if task_class == "direct" or direct_exception
        else "visible_task" if isolation
        else "subagent"
    )
    execution_mode = {
        "direct": "root", "subagent": "delegated", "visible_task": "isolated",
    }[execution_target]
    dispatch_blocker = "none"
    if no_delegation_quality_block:
        dispatch_blocker = "quality_floor_requires_specialist"
    elif execution_target == "visible_task" and not caller_is_root:
        dispatch_blocker = "visible_task_root_only"
    elif execution_target == "visible_task" and routing_context.get("visible_task_available") is False:
        dispatch_blocker = "visible_task_unavailable"
    elif execution_target == "subagent" and routing_context.get("worker_available") is False:
        dispatch_blocker = "worker_unavailable"
    elif execution_target != "direct" and delegation_depth >= MAX_DELEGATION_DEPTH:
        dispatch_blocker = "delegation_depth_exceeded"
    primary_stage_hint = {
        "direct": "synthesize",
        "discovery": "collect",
        "execution": "collect",
        "implementation": "implement",
    }.get(task_class, "unknown")
    primary_delegation_depth_hint = (
        delegation_depth
        if execution_target == "direct"
        else min(delegation_depth + 1, MAX_DELEGATION_DEPTH)
    )
    scoped_override = _active_overrides(
        load_policy(root),
        selected,
        task_class,
        {
            "role": role,
            "model": model,
            "reasoning_effort": effort,
            "execution_target": execution_target,
            "delegation_depth": primary_delegation_depth_hint,
            "stage": primary_stage_hint,
        },
    )
    if "model" in scoped_override and "model" not in constraints:
        model = str(scoped_override["model"])
    if "reasoning_effort" in scoped_override and "reasoning_effort" not in constraints:
        effort = str(scoped_override["reasoning_effort"])
        effort_basis = ["policy_override"]
    _validate_route_tuple(
        loaded,
        role,
        model,
        effort,
        explicit_effort=bool(
            constraints.get("reasoning_effort")
            or "reasoning_effort" in scoped_override
        ),
    )
    if direct_exception:
        role = "direct"
        model = "gpt-5.6-sol"
        effort = "medium"
        floor = "gpt-5.6-sol"

    exceptional = _contains(_normalise(task), EXCEPTIONAL_TERMS)
    add_audit = (
        task_class == "audit"
        or features["decision_impact"] in {"high", "critical"}
        or exceptional
    )
    if exceptional and "high_impact_exceptional_result" not in effort_basis:
        effort_basis.append("high_impact_exceptional_result")
    stages = _route_stages(
        loaded,
        "direct" if direct_exception else task_class,
        effort,
        add_audit=add_audit,
        needs_implementation=(
            task_class in {"research", "diagnosis"}
            and features["operation_mode"] == "change"
            and features["spec_state"] == "frozen"
        ),
        delegation_depth=delegation_depth,
        worker_target=execution_target if execution_target != "direct" else "subagent",
        independent_read_only_count=int(
            routing_context.get("independent_read_only_count", 1)
        ),
    )
    primary_stage_name = None if direct_exception else {
        "discovery": "collect",
        "execution": "collect",
        "implementation": "implement",
    }.get(task_class)
    if primary_stage_name:
        primary_stage = next(stage for stage in stages if stage["stage"] == primary_stage_name)
        primary_stage.update(model=model, reasoning_effort=effort)
    if capability_exception:
        for stage in stages:
            if stage["authority"] == "evidence" and MODEL_ORDER[str(requested_model)] >= MODEL_ORDER[stage["capability_floor"]]:
                stage["model"] = str(requested_model)
                break
    escalation = list(config.get("sol_escalation_conditions") or [])
    if config["authority"] in {"evidence", "implementation"} and not escalation:
        escalation = ["undefined semantics", "unresolved research conclusion", "irreversible decision"]
    return RoutePlan(
        route_id=route_id or str(uuid.uuid4()),
        profile=selected,
        task_class=task_class,
        role=role,
        model=model,
        reasoning_effort=effort,
        confidence=round(float(features["confidence"]), 2),
        reasons=["structured decision features", f"cognitive_type={features['cognitive_type']}"],
        escalation_triggers=escalation,
        output_contract="Return the bounded stage result; named Sol specialists may supply delegated decisions or audits, while the Root stays Sol Medium and owns intent, integration, acceptance, and the user-facing conclusion.",
        decision_features=features,
        constraints=constraints,
        plan_version=3,
        profile_version=4,
        capability_floor=floor,
        effort_basis=effort_basis,
        route_mode="single" if len(stages) == 1 else "staged",
        stages=stages,
        capability_exception=capability_exception,
        execution_target=execution_target,
        execution_mode=execution_mode,
        delegation_depth=delegation_depth,
        writer_ownership={
            "mode": "single_writer",
            "owner": next(
                (
                    str(stage["role"])
                    for stage in stages
                    if stage["writer_mode"] == "single_writer"
                ),
                "direct",
            ),
        },
        dispatch_ready=dispatch_blocker == "none",
        dispatch_blocker=dispatch_blocker,
        token_estimate=_token_estimate(
            features,
            token_context,
            prefer_direct=execution_target == "direct",
            quality_requires_specialist=quality_requires_specialist,
            visible_task=execution_target == "visible_task",
            blocked=dispatch_blocker != "none",
        ),
        handoff_contract={
            "input_contract": "frozen_scope",
            "deliverable": "bounded_result",
            "acceptance": "root_quality_gate",
            "failure_disposition": "freeze_and_reroute",
        },
        visible_task_title=(
            _visible_task_title(model, effort, task_class)
            if execution_target == "visible_task" else None
        ),
        shadow_recommendation=_shadow_recommendation(
            selected,
            task_class,
            root,
            {
                "role": role,
                "model": model,
                "reasoning_effort": effort,
                "execution_target": execution_target,
                "delegation_depth": primary_delegation_depth_hint,
                "stage": primary_stage_hint,
            },
        ),
    )


class RouterEngine:
    """The only public engine seam: begin, plan, observe, finalize, evaluate, status."""

    def __init__(self, root: Path | None = None):
        self.root = root or plugin_data_root()

    def _ledger(self) -> dict[str, Any]:
        value = _read_json(
            ledger_path(self.root),
            {"schema_version": 2, "next_sequence": 1, "tasks": {}, "dedupe": {}},
        )
        if not isinstance(value, dict):
            value = {"schema_version": 2, "next_sequence": 1, "tasks": {}, "dedupe": {}}
        value.setdefault("tasks", {})
        value.setdefault("dedupe", {})
        value.setdefault("next_sequence", 1)
        for event in _read_jsonl(events_path(self.root)):
            value["next_sequence"] = max(
                int(value["next_sequence"]), int(event.get("sequence") or 0) + 1
            )
            if event.get("dedupe_key") and event.get("event_id"):
                value["dedupe"][event["dedupe_key"]] = event["event_id"]
            reference = event.get("task_ref")
            if (
                event.get("type") == "route"
                and reference
                and reference not in value["tasks"]
            ):
                route = {
                    k: event.get(k)
                    for k in (
                        "route_id",
                        "profile",
                        "task_class",
                        "role",
                        "model",
                        "reasoning_effort",
                        "confidence",
                        "decision_features",
                        "constraints",
                        "policy_revision",
                        "shadow",
                        "plan_version",
                        "profile_version",
                        "capability_floor",
                        "effort_basis",
                        "route_mode",
                        "stages",
                        "capability_exception",
                        "execution_target",
                        "execution_mode",
                        "delegation_depth",
                        "writer_ownership",
                        "dispatch_ready",
                        "dispatch_blocker",
                        "token_estimate",
                        "handoff_contract",
                    )
                }
                route["visible_task_title"] = (
                    _visible_task_title(
                        str(route.get("model")),
                        str(route.get("reasoning_effort")),
                        str(route.get("task_class")),
                    )
                    if route.get("execution_target") == "visible_task"
                    else None
                )
                value["tasks"][reference] = {
                    "task_ref": reference,
                    "route_id": event.get("route_id"),
                    "session": event.get("session"),
                    "project": event.get("project"),
                    "turn": None,
                    "status": "active",
                    "started_at": event.get("created_at") or utc_now(),
                    "started_sequence": event.get("sequence", 0),
                    "route": route,
                    "aggregate": {
                        "tool_count": 0,
                        "failure_count": 0,
                        "retry_count": 0,
                        "verification_kinds": [],
                        "transitions": [],
                        "lifecycle": {},
                        "leases": {},
                    },
                }
        for task in value["tasks"].values():
            aggregate = task.setdefault("aggregate", {})
            aggregate.setdefault("tool_count", 0)
            aggregate.setdefault("failure_count", 0)
            aggregate.setdefault("retry_count", 0)
            aggregate.setdefault("verification_kinds", [])
            aggregate.setdefault("transitions", [])
            aggregate.setdefault("lifecycle", {})
            aggregate.setdefault("leases", {})
        return value

    def _append(
        self,
        ledger: dict[str, Any],
        record: dict[str, Any],
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        if dedupe_key and dedupe_key in ledger["dedupe"]:
            event_id = ledger["dedupe"][dedupe_key]
            return next(
                (
                    x
                    for x in reversed(_read_jsonl(events_path(self.root)))
                    if x.get("event_id") == event_id
                ),
                record,
            )
        value = dict(record)
        if value.get("type") == "route":
            value.pop("visible_task_title", None)
        value.update(
            schema_version=4,
            event_id=str(uuid.uuid4()),
            sequence=int(ledger["next_sequence"]),
            created_at=utc_now(),
        )
        ledger["next_sequence"] = value["sequence"] + 1
        if dedupe_key:
            ledger["dedupe"][dedupe_key] = value["event_id"]
        if dedupe_key:
            value["dedupe_key"] = dedupe_key
        validate_evidence_event(value)
        _append_unlocked(events_path(self.root), value)
        return value

    @staticmethod
    def _route_payload(plan: RoutePlan) -> dict[str, Any]:
        return {
            "route_id": plan.route_id,
            "profile": plan.profile,
            "task_class": plan.task_class,
            "role": plan.role,
            "model": plan.model,
            "reasoning_effort": plan.reasoning_effort,
            "confidence": plan.confidence,
            "decision_features": plan.decision_features,
            "constraints": plan.constraints,
            "plan_version": plan.plan_version,
            "profile_version": plan.profile_version,
            "capability_floor": plan.capability_floor,
            "effort_basis": plan.effort_basis,
            "route_mode": plan.route_mode,
            "stages": plan.stages,
            "capability_exception": plan.capability_exception,
            "execution_target": plan.execution_target,
            "execution_mode": plan.execution_mode,
            "delegation_depth": plan.delegation_depth,
            "writer_ownership": plan.writer_ownership,
            "dispatch_ready": plan.dispatch_ready,
            "dispatch_blocker": plan.dispatch_blocker,
            "token_estimate": plan.token_estimate,
            "handoff_contract": plan.handoff_contract,
            "visible_task_title": plan.visible_task_title,
            "policy_revision": 1,
            "shadow": plan.shadow_recommendation,
        }

    @staticmethod
    def _latest_task(ledger: dict[str, Any], session: str) -> dict[str, Any] | None:
        return max(
            (x for x in ledger["tasks"].values() if x.get("session") == session),
            key=lambda x: (
                int(x.get("started_sequence") or 0),
                x.get("started_at", ""),
            ),
            default=None,
        )

    def begin_task(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        project: str | None = None,
        decision_features: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        routing_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = identity(f"task:{session_id}:{turn_id}", self.root)
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            if key in ledger["tasks"]:
                return dict(ledger["tasks"][key])
            previous = self._latest_task(ledger, identity(session_id, self.root))
            if previous and previous.get("status") == "provisional":
                self._observe_followup(ledger, previous, prompt)
            plan = make_route_plan(
                prompt,
                decision_features=decision_features,
                constraints=constraints,
                routing_context=routing_context,
                root=self.root,
            )
            route = self._route_payload(plan)
            task = {
                "task_ref": key,
                "route_id": plan.route_id,
                "session": identity(session_id, self.root),
                "turn": identity(turn_id, self.root),
                "project": identity(project or "unspecified", self.root),
                "status": "active",
                "started_at": utc_now(),
                "started_sequence": int(ledger["next_sequence"]),
                "route": route,
                "aggregate": {
                    "tool_count": 0,
                    "failure_count": 0,
                    "retry_count": 0,
                    "verification_kinds": [],
                    "transitions": [],
                    "lifecycle": {},
                    "leases": {},
                },
            }
            ledger["tasks"][key] = task
            self._append(
                ledger,
                {
                    "type": "route",
                    "task_ref": key,
                    "task_fingerprint": identity(prompt, self.root),
                    **route,
                    "session": task["session"],
                    "project": task["project"],
                },
                f"route:{key}",
            )
            _atomic_write_json(ledger_path(self.root), ledger)
            return dict(task)

    def _observe_followup(
        self, ledger: dict[str, Any], previous: dict[str, Any], prompt: str
    ) -> None:
        text = _normalise(prompt)
        label = (
            "corrected"
            if _contains(text, CORRECTION_TERMS)
            else "overridden" if _contains(text, OVERRIDE_TERMS) else None
        )
        if not label:
            return
        previous["status"] = label
        self._append(
            ledger,
            {
                "type": "outcome",
                "task_ref": previous["task_ref"],
                "route_id": previous["route_id"],
                "status": label,
                "quality_gate": "failed",
                "route_fit": "under_routed" if label == "corrected" else "unknown",
                "verification_kinds": [],
                "confidence": 0.9,
                "evidence_source": "user_explicit",
                "user_confirmed": True,
                "objective_verification": False,
                "replacement": None,
                "high_risk_regression": False,
                "retry_band": "unknown",
                "rework_band": "medium",
                "tool_band": "unknown",
                "duration_band": "unknown",
                "token_band": "unknown",
                "cost_band": "unknown",
                "model_fit": "unknown",
                "effort_fit": "unknown",
                "context_fit": "unknown",
                "tool_data_fit": "unknown",
                "failure_axis": "execution",
                "result_signal": "unknown",
                "stage_source": "unknown",
                "dispatch_mode": previous["route"].get("execution_target", "direct"),
                "observed_execution": {
                    "role": "unknown", "model": "unknown",
                    "reasoning_effort": "unknown", "execution_target": "unknown",
                },
                "plan_match": "unknown",
                "boundary_status": "unknown",
                "scope_status": "unknown",
                "verification_status": "failed",
                "archive_status": "not_ready",
                "delegation_depth": int(previous["route"].get("delegation_depth") or 0),
                "stage_lease": {"lease_id": "unknown", "status": "unknown"},
            },
            f"followup:{previous['task_ref']}:{label}",
        )

    @staticmethod
    def _event_payload(
        event: dict[str, Any], *, ignore_event_id: bool = False
    ) -> dict[str, Any]:
        ignored = {"sequence"}
        if ignore_event_id:
            ignored.add("event_id")
        return {key: value for key, value in event.items() if key not in ignored}

    def _legacy_task_events(self, task_ref: str) -> list[dict[str, Any]]:
        if self.root.resolve(strict=False) != plugin_data_root().resolve(strict=False):
            return []
        by_event_id: dict[str, dict[str, Any]] = {}
        by_dedupe_key: dict[str, dict[str, Any]] = {}
        route_ids: set[str] = set()
        for root in legacy_plugin_data_roots(self.root):
            with _file_lock(ledger_path(root)):
                events = [
                    event
                    for event in _read_jsonl_strict(events_path(root), "legacy")
                    if event.get("task_ref") == task_ref
                ]
            for event in events:
                validate_evidence_event(event)
                if event["type"] == "route":
                    route_ids.add(str(event["route_id"]))
                event_id = str(event["event_id"])
                previous = by_event_id.get(event_id)
                if previous is not None:
                    if self._event_payload(previous) != self._event_payload(event):
                        raise ValueError(
                            f"conflicting legacy event_id for task_ref {task_ref}"
                        )
                    continue
                dedupe_key = event.get("dedupe_key")
                if dedupe_key:
                    previous = by_dedupe_key.get(str(dedupe_key))
                    if previous is not None:
                        if self._event_payload(
                            previous, ignore_event_id=True
                        ) != self._event_payload(event, ignore_event_id=True):
                            raise ValueError(
                                f"conflicting legacy dedupe_key for task_ref {task_ref}"
                            )
                        continue
                    by_dedupe_key[str(dedupe_key)] = event
                by_event_id[event_id] = event
        if len(route_ids) > 1:
            raise ValueError(f"conflicting legacy route_id for task_ref {task_ref}")
        if not route_ids:
            return []
        return sorted(
            by_event_id.values(),
            key=lambda event: (
                int(event.get("sequence") or 0),
                str(event.get("created_at") or ""),
                str(event.get("event_id") or ""),
            ),
        )

    def _import_legacy_task(self, task_ref: str, ledger: dict[str, Any]) -> bool:
        legacy_events = self._legacy_task_events(task_ref)
        if not legacy_events:
            return False
        current_events = _read_jsonl_strict(
            events_path(self.root), "canonical"
        )
        previous_sequence = 0
        seen_event_ids: set[str] = set()
        seen_dedupe_keys: set[str] = set()
        for event in current_events:
            validate_evidence_event(event)
            sequence = int(event["sequence"])
            if sequence <= previous_sequence:
                raise ValueError("canonical event sequence is not strictly increasing")
            previous_sequence = sequence
            event_id = str(event["event_id"])
            if event_id in seen_event_ids:
                raise ValueError("duplicate canonical event_id")
            seen_event_ids.add(event_id)
            dedupe_key = event.get("dedupe_key")
            if dedupe_key and str(dedupe_key) in seen_dedupe_keys:
                raise ValueError("duplicate canonical dedupe_key")
            if dedupe_key:
                seen_dedupe_keys.add(str(dedupe_key))
        by_event_id = {str(event.get("event_id")): event for event in current_events}
        by_dedupe_key = {
            str(event["dedupe_key"]): event
            for event in current_events
            if event.get("dedupe_key")
        }
        missing: list[dict[str, Any]] = []
        next_sequence = max(
            [int(ledger.get("next_sequence") or 1)]
            + [int(event.get("sequence") or 0) + 1 for event in current_events]
        )
        for event in legacy_events:
            event_id = str(event["event_id"])
            existing = by_event_id.get(event_id)
            if existing is not None:
                if self._event_payload(existing) != self._event_payload(event):
                    raise ValueError(
                        f"conflicting canonical event_id for task_ref {task_ref}"
                    )
                continue
            dedupe_key = event.get("dedupe_key")
            if dedupe_key and str(dedupe_key) in by_dedupe_key:
                existing = by_dedupe_key[str(dedupe_key)]
                if self._event_payload(
                    existing, ignore_event_id=True
                ) != self._event_payload(event, ignore_event_id=True):
                    raise ValueError(
                        f"conflicting canonical dedupe_key for task_ref {task_ref}"
                    )
                continue
            imported = dict(event)
            imported["sequence"] = next_sequence
            next_sequence += 1
            validate_evidence_event(imported)
            missing.append(imported)
            by_event_id[event_id] = imported
            if dedupe_key:
                by_dedupe_key[str(dedupe_key)] = imported
        if missing:
            _atomic_write_jsonl(events_path(self.root), current_events + missing)
        rebuilt = self._ledger()
        if task_ref not in rebuilt["tasks"]:
            raise ValueError(f"legacy route event missing for task_ref {task_ref}")
        _atomic_write_json(ledger_path(self.root), rebuilt)
        return True

    def plan_route(
        self,
        task: str | None = None,
        *,
        task_ref: str | None = None,
        session_id: str | None = None,
        project_fingerprint: str | None = None,
        project_identity: str | None = None,
        parent_task_ref: str | None = None,
        parent_lease_id: str | None = None,
        profile: str | None = None,
        task_state: str = "unknown",
        force_role: str | None = None,
        decision_features: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        routing_context: dict[str, Any] | None = None,
        record: bool = True,
    ) -> dict[str, Any]:
        if project_identity is not None and (
            not isinstance(project_identity, str) or not HEX_ID.fullmatch(project_identity)
        ):
            raise ValueError("project_identity is invalid")
        if parent_task_ref is not None and (
            not isinstance(parent_task_ref, str) or not HEX_ID.fullmatch(parent_task_ref)
        ):
            raise ValueError("parent_task_ref is invalid")
        if parent_lease_id is not None and (
            not isinstance(parent_lease_id, str) or not HEX_ID.fullmatch(parent_lease_id)
        ):
            raise ValueError("parent_lease_id is invalid")
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            if (
                task_ref
                and task_ref not in ledger["tasks"]
                and self._import_legacy_task(task_ref, ledger)
            ):
                ledger = self._ledger()
            if task_ref and task_ref in ledger["tasks"]:
                return dict(ledger["tasks"][task_ref]["route"])
            if task is None or not task.strip():
                if task_ref:
                    raise ValueError("unknown task_ref; task is required to create a new route")
                raise ValueError("task is required when task_ref is omitted")
            plan = make_route_plan(
                task,
                profile=profile,
                task_state=task_state,
                force_role=force_role,
                decision_features=decision_features,
                constraints=constraints,
                routing_context=routing_context,
                root=self.root,
            )
            value = self._route_payload(plan)
            if record:
                reference = task_ref or identity(f"manual:{plan.route_id}", self.root)
                task_record = {
                    "task_ref": reference,
                    "route_id": plan.route_id,
                    "session": identity(session_id or "manual", self.root),
                    "turn": identity(plan.route_id, self.root),
                    "project": project_identity or identity(
                        project_fingerprint or "unspecified", self.root
                    ),
                    "parent_task_ref": parent_task_ref,
                    "parent_lease_id": parent_lease_id,
                    "status": "active",
                    "started_at": utc_now(),
                    "started_sequence": int(ledger["next_sequence"]),
                    "route": value,
                    "aggregate": {
                        "tool_count": 0,
                        "failure_count": 0,
                        "retry_count": 0,
                        "verification_kinds": [],
                        "transitions": [],
                        "lifecycle": {},
                        "leases": {},
                    },
                }
                ledger["tasks"][reference] = task_record
                self._append(
                    ledger,
                    {
                        "type": "route",
                        "task_ref": reference,
                        "task_fingerprint": identity(task, self.root),
                        **value,
                        "session": task_record["session"],
                        "project": task_record["project"],
                    },
                    f"route:{reference}",
                )
                _atomic_write_json(ledger_path(self.root), ledger)
                value["task_ref"] = reference
            return value

    @staticmethod
    def _find_lease(
        ledger: dict[str, Any], lease_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for task in ledger["tasks"].values():
            leases = task.get("aggregate", {}).get("leases", {})
            lease = leases.get(lease_id) if isinstance(leases, dict) else None
            if isinstance(lease, dict):
                return task, lease
        return None

    def dispatch_stage(
        self,
        task_ref: str,
        stage: str,
        *,
        parent_lease_id: str | None = None,
        independent_read_only: bool = False,
        independence_key: str | None = None,
        caller_is_root: bool = False,
        worker_available: bool = True,
        visible_task_available: bool = True,
        visible_task_title: str | None = None,
        objective: str | None = None,
    ) -> dict[str, Any]:
        """Acquire one bounded stage lease before dispatching work."""
        if type(caller_is_root) is not bool or type(worker_available) is not bool or type(visible_task_available) is not bool:
            raise ValueError("dispatch caller/readiness flags must be boolean")
        if independence_key is not None and (
            not isinstance(independence_key, str)
            or not independence_key.strip()
            or len(independence_key) > 120
        ):
            raise ValueError("independence_key is invalid")
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            task = ledger["tasks"].get(task_ref)
            if not task:
                raise ValueError("unknown task_ref")
            route = task["route"]
            if not route.get("dispatch_ready", True):
                raise ValueError(f"dispatch blocked: {route.get('dispatch_blocker', 'unknown')}")
            planned = next(
                (
                    item for item in route.get("stages", [])
                    if stage in {item.get("stage"), item.get("stage_id")}
                    and item.get("required") is True
                ),
                None,
            )
            if not isinstance(planned, dict):
                followup = self._audit_followup_for_task(task_ref)
                if (
                    isinstance(followup, dict)
                    and stage in {followup.get("stage"), followup.get("stage_id")}
                    and followup.get("required") is True
                ):
                    planned = followup
            if not isinstance(planned, dict):
                raise TypeError("stage is not part of task route plan")
            target = str(planned.get("execution_target") or "direct")
            depth = int(planned.get("delegation_depth") or 0)
            if depth > MAX_DELEGATION_DEPTH:
                raise ValueError("dispatch blocked: delegation_depth_exceeded")
            if target == "direct":
                raise ValueError("direct stages do not acquire a delegated lease")
            if target == "subagent" and not worker_available:
                raise ValueError("dispatch blocked: worker_unavailable")
            if target == "visible_task" and not visible_task_available:
                raise ValueError("dispatch blocked: visible_task_unavailable")
            if target == "visible_task":
                if not caller_is_root or int(route.get("delegation_depth") or 0) != 0:
                    raise ValueError("dispatch blocked: visible_task_root_only")
                title = visible_task_title or route.get("visible_task_title")
                if not isinstance(title, str) or not VISIBLE_TASK_TITLE.fullmatch(title):
                    raise ValueError("dispatch blocked: visible_task_title_invalid")
            if int(route.get("delegation_depth") or 0) > 0:
                if not parent_lease_id:
                    raise ValueError("dispatch blocked: parent_lease_required")
                found = self._find_lease(ledger, parent_lease_id)
                if (
                    not found
                    or found[1].get("status") not in {"active", "frozen"}
                    or found[0].get("task_ref") != task.get("parent_task_ref")
                    or found[1].get("lease_id") != task.get("parent_lease_id")
                    or found[0].get("project") != task.get("project")
                ):
                    raise ValueError("dispatch blocked: parent_lease_inactive")
            parent_key = parent_lease_id or f"root:{task_ref}"
            active = []
            active_writers = []
            for candidate_task in ledger["tasks"].values():
                for lease in candidate_task.get("aggregate", {}).get("leases", {}).values():
                    if lease.get("status") != "active":
                        continue
                    if lease.get("parent") == parent_key:
                        active.append(lease)
                    if (
                        lease.get("writer_mode") == "single_writer"
                        and lease.get("repository_hmac") == task.get("project")
                    ):
                        active_writers.append(lease)
            writer_mode = str(planned.get("writer_mode") or "read_only")
            independence_hmac = (
                identity(f"independence:{independence_key.strip()}", self.root)
                if independence_key is not None
                else None
            )
            if independent_read_only and (
                writer_mode != "read_only"
                or planned.get("parallelism") != "independent_read_only"
                or independence_hmac is None
            ):
                raise ValueError("dispatch blocked: independent_read_only_not_declared")
            if writer_mode == "single_writer" and active_writers:
                raise ValueError("dispatch blocked: writer_lease_conflict")
            if writer_mode == "read_only":
                limit = int(planned.get("parallel_limit") or 1) if independent_read_only else 1
                if len(active) >= limit:
                    raise ValueError("dispatch blocked: read_only_concurrency_exceeded")
                if independent_read_only and any(
                    lease.get("writer_mode") != "read_only"
                    or not lease.get("independent_read_only")
                    for lease in active
                ):
                    raise ValueError("dispatch blocked: writer_lease_conflict")
                if independent_read_only and any(
                    lease.get("independence_hmac") == independence_hmac
                    for lease in active
                ):
                    raise ValueError("dispatch blocked: duplicate_independence_key")
            lease_id = identity(
                f"lease:{task_ref}:{stage}:{uuid.uuid4()}", self.root
            )
            lease = {
                "lease_id": lease_id,
                "task_ref": task_ref,
                "stage": planned["stage"],
                "stage_id": planned["stage_id"],
                "attempt": planned["attempt"],
                "status": "active",
                "parent": parent_key,
                "delegation_depth": depth,
                "execution_target": target,
                "role": planned["role"],
                "model": planned["model"],
                "reasoning_effort": planned["reasoning_effort"],
                "writer_mode": writer_mode,
                "repository_hmac": task.get("project"),
                "independent_read_only": bool(independent_read_only),
                "independence_hmac": independence_hmac,
                "started_at": utc_now(),
            }
            task["aggregate"].setdefault("leases", {})[lease_id] = lease
            _atomic_write_json(ledger_path(self.root), ledger)
            result = dict(lease)
            result["agent_package"] = {
                "objective": objective or "bounded_stage_objective",
                "stage_id": planned["stage_id"],
                "lease_id": lease_id,
                "parent_lease_id": parent_lease_id,
                "delegation_depth": depth,
                "role": planned["role"],
                "model": planned["model"],
                "reasoning_effort": planned["reasoning_effort"],
                "authority": planned["authority"],
                "access_mode": planned["access_mode"],
                "ownership": "single_repository_writer" if writer_mode == "single_writer" else "read_only",
                "deliverable_contract": "bounded_result",
                "verification_contract": "objective_quality_gate",
                "failure_disposition": "freeze_and_reroute",
                "escalation_contract": "freeze_and_reroute_on_scope_or_semantic_mismatch",
                "handback_contract": "return_to_parent_for_acceptance",
            }
            return result

    def complete_stage(
        self,
        task_ref: str,
        lease_id: str,
        *,
        success: bool,
        quality_gate: str,
        archive: bool = False,
        observed_role: str | None = None,
        observed_model: str | None = None,
        observed_effort: str | None = None,
        observed_execution_target: str | None = None,
        observed_source: str | None = None,
        boundary_status: str = "unknown",
        scope_status: str = "unknown",
        verification_status: str = "unknown",
    ) -> dict[str, Any]:
        if quality_gate not in QUALITY_GATES:
            raise ValueError("invalid quality gate")
        if boundary_status not in BOUNDARY_STATUSES or scope_status not in SCOPE_STATUSES:
            raise ValueError("invalid stage boundary/scope status")
        if verification_status not in VERIFICATION_STATUSES:
            raise ValueError("invalid stage verification status")
        observed_values = (
            observed_role, observed_model, observed_effort,
            observed_execution_target, observed_source,
        )
        if any(value is not None for value in observed_values) and not all(
            value is not None for value in observed_values
        ):
            raise ValueError("observed stage provenance must be supplied as a complete tuple")
        if observed_role is not None and (
            observed_role not in VALID_ROLES
            or observed_model not in VALID_MODELS
            or observed_effort not in VALID_EFFORTS
            or observed_execution_target not in EXECUTION_TARGETS
            or observed_source not in {"caller_supplied", "provider_hook"}
        ):
            raise ValueError("invalid observed stage provenance")
        if archive:
            raise ValueError("archive must be confirmed later with archive_observed after terminal acceptance")
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            task = ledger["tasks"].get(task_ref)
            if not task:
                raise ValueError("unknown task_ref")
            lease = task.get("aggregate", {}).get("leases", {}).get(lease_id)
            if not isinstance(lease, dict) or lease.get("status") != "active":
                raise ValueError("stage lease is not active")
            lease["status"] = "completed" if success else "failed"
            lease["completed_at"] = utc_now()
            lease["quality_gate"] = quality_gate
            lease["boundary_status"] = boundary_status
            lease["scope_status"] = scope_status
            lease["verification_status"] = verification_status
            if observed_role is not None:
                lease["observed_execution"] = {
                    "role": observed_role,
                    "model": observed_model,
                    "reasoning_effort": observed_effort,
                    "execution_target": observed_execution_target,
                    "source": observed_source,
                }
            archive_status = "not_applicable"
            if lease["execution_target"] == "visible_task":
                archive_status = "not_ready"
            lease["archive_status"] = archive_status
            if observed_role is not None:
                self._append(
                    ledger,
                    {
                        "type": "execution",
                        "task_ref": task_ref,
                        "route_id": task["route_id"],
                        "event": "SubagentStop",
                        "tool_kind": "lifecycle",
                        "failed": not success,
                        "transition": {
                            "phase": "stop",
                            "role": observed_role,
                            "model": observed_model,
                            "reasoning_effort": observed_effort,
                            "stage": lease["stage"],
                            "execution_target": observed_execution_target,
                            "delegation_depth": lease["delegation_depth"],
                            "lease_id": lease_id,
                            "writer_mode": lease["writer_mode"],
                        },
                    },
                    f"lease-stop:{lease_id}:{lease['status']}",
                )
            _atomic_write_json(ledger_path(self.root), ledger)
            return dict(lease)

    def freeze_and_reroute(
        self,
        task_ref: str,
        lease_id: str,
        remaining_task: str,
        *,
        decision_features: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not remaining_task.strip():
            raise ValueError("remaining_task must not be empty")
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            task = ledger["tasks"].get(task_ref)
            lease = (
                task.get("aggregate", {}).get("leases", {}).get(lease_id)
                if task else None
            )
            if not isinstance(lease, dict) or lease.get("status") not in {"active", "frozen"}:
                raise ValueError("stage lease cannot be rerouted")
            lease["status"] = "frozen"
            lease["frozen_at"] = lease.get("frozen_at") or utc_now()
            depth = int(lease["delegation_depth"])
            _atomic_write_json(ledger_path(self.root), ledger)
        reroute_ref = identity(
            f"reroute:{task_ref}:{lease_id}:{uuid.uuid4()}", self.root
        )
        return self.plan_route(
            remaining_task,
            task_ref=reroute_ref,
            project_identity=str(task["project"]),
            parent_task_ref=task_ref,
            parent_lease_id=lease_id,
            profile=str(task["route"].get("profile") or "generic"),
            task_state="frozen",
            decision_features=decision_features,
            constraints=constraints,
            routing_context={
                "delegation_depth": depth,
                "caller_is_root": False,
                "parent_lease_id": lease_id,
            },
        )

    def transition_stage(
        self,
        task_ref: str,
        lease_id: str,
        action: str,
        *,
        quality_gate: str = "unknown",
        remaining_task: str | None = None,
        observed_role: str | None = None,
        observed_model: str | None = None,
        observed_effort: str | None = None,
        observed_execution_target: str | None = None,
        observed_source: str | None = None,
        boundary_status: str = "unknown",
        scope_status: str = "unknown",
        verification_status: str = "unknown",
    ) -> dict[str, Any]:
        if action == "complete":
            return self.complete_stage(
                task_ref, lease_id, success=True, quality_gate=quality_gate,
                observed_role=observed_role, observed_model=observed_model,
                observed_effort=observed_effort,
                observed_execution_target=observed_execution_target,
                observed_source=observed_source,
                boundary_status=boundary_status, scope_status=scope_status,
                verification_status=verification_status,
            )
        if action == "fail":
            return self.complete_stage(
                task_ref, lease_id, success=False, quality_gate=quality_gate,
                observed_role=observed_role, observed_model=observed_model,
                observed_effort=observed_effort,
                observed_execution_target=observed_execution_target,
                observed_source=observed_source,
                boundary_status=boundary_status, scope_status=scope_status,
                verification_status=verification_status,
            )
        if action == "reroute":
            if remaining_task is None:
                raise ValueError("remaining_task is required for reroute")
            return self.freeze_and_reroute(task_ref, lease_id, remaining_task)
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            task = ledger["tasks"].get(task_ref)
            lease = (
                task.get("aggregate", {}).get("leases", {}).get(lease_id)
                if task else None
            )
            if not isinstance(lease, dict):
                raise TypeError("unknown stage lease")
            if action == "freeze":
                if lease.get("status") != "active":
                    raise ValueError("only an active stage lease may freeze")
                lease["status"] = "frozen"
                lease["frozen_at"] = utc_now()
            elif action == "release":
                if lease.get("status") not in {"active", "frozen", "failed"}:
                    raise ValueError("stage lease cannot be released")
                lease["status"] = "released"
                lease["closed_at"] = utc_now()
            elif action == "archive_observed":
                if not self._visible_archive_eligible(task):
                    raise ValueError("visible task is not archive eligible")
                lease["archive_status"] = "eligible"
                lease["archive_status"] = "archived"
                self._append(
                    ledger,
                    {
                        "type": "execution",
                        "task_ref": task_ref,
                        "route_id": task["route_id"],
                        "event": "ArchiveObserved",
                        "tool_kind": "lifecycle",
                        "archive_status": "archived",
                    },
                    f"archive-observed:{lease_id}",
                )
            else:
                raise ValueError("invalid stage transition action")
            _atomic_write_json(ledger_path(self.root), ledger)
            return dict(lease)

    def _visible_archive_eligible(self, task: dict[str, Any]) -> bool:
        required_ids = {
            str(stage.get("stage_id"))
            for stage in task["route"].get("stages", [])
            if stage.get("required") is True
            and stage.get("execution_target") == "visible_task"
        }
        leases = list(task.get("aggregate", {}).get("leases", {}).values())
        passed_ids = {
            str(item.get("stage_id"))
            for item in leases
            if item.get("status") == "completed"
            and item.get("quality_gate") == "passed"
            and item.get("boundary_status") == "passed"
            and item.get("scope_status") == "passed"
            and item.get("verification_status") == "passed"
            and isinstance(item.get("observed_execution"), dict)
        }
        outcomes = [
            event for event in _read_jsonl(events_path(self.root))
            if event.get("type") == "outcome"
            and event.get("task_ref") == task.get("task_ref")
        ]
        latest_outcome = next(
            (
                event for event in reversed(outcomes)
            ),
            None,
        )
        followup_sequences = [
            int(event.get("sequence") or 0)
            for event in outcomes
            if isinstance(event.get("audit_followup"), dict)
        ]
        outstanding_audit = bool(followup_sequences) and not any(
            int(event.get("sequence") or 0) > max(followup_sequences)
            and event.get("stage") == "audit"
            and event.get("status") in {"completed", "verified"}
            and event.get("quality_gate") == "passed"
            and event.get("objective_verification") is True
            and event.get("boundary_status") == "passed"
            and event.get("scope_status") == "passed"
            and event.get("verification_status") == "passed"
            and event.get("plan_match") == "matched"
            and isinstance(event.get("stage_lease"), dict)
            and event["stage_lease"].get("lease_id") in task.get("aggregate", {}).get("leases", {})
            and task["aggregate"]["leases"][event["stage_lease"]["lease_id"]].get("stage") == "audit"
            and task["aggregate"]["leases"][event["stage_lease"]["lease_id"]].get("status") == "completed"
            and task["aggregate"]["leases"][event["stage_lease"]["lease_id"]].get("quality_gate") == "passed"
            and isinstance(
                task["aggregate"]["leases"][event["stage_lease"]["lease_id"]].get("observed_execution"),
                dict,
            )
            for event in outcomes
        )
        return bool(
            required_ids
            and required_ids <= passed_ids
            and isinstance(latest_outcome, dict)
            and latest_outcome.get("status") in {"completed", "verified"}
            and latest_outcome.get("quality_gate") == "passed"
            and latest_outcome.get("objective_verification") is True
            and latest_outcome.get("boundary_status") == "passed"
            and latest_outcome.get("scope_status") == "passed"
            and latest_outcome.get("verification_status") == "passed"
            and not outstanding_audit
        )

    @staticmethod
    def _tool_failed(response: Any) -> bool:
        if not isinstance(response, dict):
            return False
        if response.get("isError") is True or response.get("is_error") is True:
            return True
        try:
            return int(response.get("exit_code", response.get("exitCode", 0)) or 0) != 0
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _tool_kind(name: str) -> str:
        low = name.casefold()
        if name in {"Bash", "exec_command"}:
            return "shell"
        if name == "apply_patch":
            return "edit"
        if "agent" in low:
            return "agent"
        if low.startswith("mcp__"):
            return "mcp"
        return "local"

    @staticmethod
    def _verification_kind(payload: dict[str, Any]) -> str | None:
        name = str(payload.get("tool_name") or "").casefold()
        raw = json.dumps(payload.get("tool_input") or {}, ensure_ascii=False).casefold()
        if "test" in raw or "pytest" in raw or "unittest" in raw:
            return "tests"
        if "compile" in raw or "build" in raw:
            return "build"
        if "lint" in raw or "validate" in raw:
            return "static_validation"
        if "review" in name:
            return "review"
        return None

    @staticmethod
    def _transition_from_tool(payload: dict[str, Any]) -> dict[str, Any] | None:
        if str(payload.get("tool_name")) not in {"Agent", "spawn_agent"}:
            return None
        value = (
            payload.get("tool_input")
            if isinstance(payload.get("tool_input"), dict)
            else {}
        )
        return {
            "phase": "start",
            "role": RouterEngine._safe_role(
                value.get("agent_type") or value.get("role")
            ),
            "model": RouterEngine._safe_model(value.get("model")),
            "reasoning_effort": RouterEngine._safe_effort(
                value.get("reasoning_effort") or value.get("effort")
            ),
        }

    @staticmethod
    def _raw_agent_identity(payload: dict[str, Any]) -> str | None:
        sources = [payload]
        for key in ("tool_input", "tool_response"):
            value = payload.get(key)
            if isinstance(value, dict):
                sources.append(value)
                structured = value.get("structuredContent")
                if isinstance(structured, dict):
                    sources.append(structured)
        for source in sources:
            for key in ("agent_id", "agentId", "subagent_id", "subagentId"):
                value = source.get(key)
                if isinstance(value, (str, int)) and str(value):
                    return str(value)
        return None

    def _required_task_stages(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        stages = [
            dict(item)
            for item in task["route"].get("stages", [])
            if isinstance(item, dict) and item.get("required") is True
        ]
        followup = self._audit_followup_for_task(str(task["task_ref"]))
        if followup and not any(item.get("stage") == "audit" for item in stages):
            stages.append(followup)
        return stages

    def _associate_lifecycle_stage(
        self,
        task: dict[str, Any],
        transition: dict[str, Any],
        agent_hash: str,
        sequence: int,
    ) -> str:
        aggregate = task["aggregate"]
        lifecycle = aggregate.setdefault("lifecycle", {})
        previous = lifecycle.get(agent_hash)
        required_stages = self._required_task_stages(task)
        stage_by_name = {
            str(item["stage"]): item for item in required_stages
        }
        stage = str(previous.get("stage", "unknown")) if previous else "unknown"
        if stage in stage_by_name:
            planned = stage_by_name[stage]
            for key in ("role", "model", "reasoning_effort"):
                observed = transition[key]
                if observed != "unknown" and observed != planned[key]:
                    stage = "unknown"
                    break
        if stage == "unknown":
            unavailable = {
                str(state.get("stage"))
                for identity_state, state in lifecycle.items()
                if identity_state != agent_hash
                and isinstance(state, dict)
                and state.get("stage") in STAGE_NAMES
            }
            unavailable.update(
                str(event["stage"])
                for event in _read_jsonl(events_path(self.root))
                if event.get("type") == "outcome"
                and event.get("task_ref") == task["task_ref"]
                and event.get("stage") in STAGE_NAMES
            )
            candidates = []
            for planned in required_stages:
                if planned["stage"] in unavailable:
                    continue
                if transition["role"] != planned["role"]:
                    continue
                if transition["model"] != "unknown" and transition["model"] != planned["model"]:
                    continue
                if (
                    transition["reasoning_effort"] != "unknown"
                    and transition["reasoning_effort"] != planned["reasoning_effort"]
                ):
                    continue
                candidates.append(str(planned["stage"]))
            if len(candidates) == 1:
                stage = candidates[0]
        status = (
            "completed"
            if transition["phase"] == "stop"
            or previous and previous.get("status") == "completed"
            else "started"
        )
        state = {
            "stage": stage,
            "role": transition["role"],
            "model": transition["model"],
            "reasoning_effort": transition["reasoning_effort"],
            "status": status,
        }
        if previous and isinstance(previous.get("started_sequence"), int):
            state["started_sequence"] = previous["started_sequence"]
        elif transition["phase"] == "start":
            state["started_sequence"] = sequence
        if status == "completed":
            state["completed_sequence"] = (
                previous.get("completed_sequence", sequence) if previous else sequence
            )
        lifecycle[agent_hash] = state
        return stage

    @staticmethod
    def _recent_completed_lifecycle_stage(task: dict[str, Any]) -> str | None:
        lifecycle = task.get("aggregate", {}).get("lifecycle", {})
        completed = [
            state
            for state in lifecycle.values()
            if isinstance(state, dict)
            and state.get("status") == "completed"
            and isinstance(state.get("completed_sequence"), int)
        ]
        if not completed:
            return None
        latest_sequence = max(int(state["completed_sequence"]) for state in completed)
        latest_stages = {
            str(state.get("stage", "unknown"))
            for state in completed
            if state["completed_sequence"] == latest_sequence
        }
        return (
            next(iter(latest_stages))
            if len(latest_stages) == 1 and latest_stages <= STAGE_NAMES
            else None
        )

    @staticmethod
    def _safe_role(value: Any) -> str:
        roles = {
            "direct",
            "default",
            "worker",
            "explorer",
            "router_code_mapper",
            "router_experiment_runner",
            "router_research_engineer",
            "router_researcher",
            "router_quant_researcher",
            "router_architect",
            "router_adversarial_auditor",
            "router_strategy_scout",
        }
        return str(value) if str(value) in roles else "unknown"

    @staticmethod
    def _safe_model(value: Any) -> str:
        return str(value) if str(value) in VALID_MODELS else "unknown"

    @staticmethod
    def _safe_effort(value: Any) -> str:
        return str(value) if str(value) in VALID_EFFORTS else "unknown"

    def observe_event(
        self, event: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        session = str(payload.get("session_id") or "unknown")
        turn = str(payload.get("turn_id") or "")
        task_ref = identity(f"task:{session}:{turn}", self.root) if turn else None
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            task = (
                ledger["tasks"].get(task_ref)
                if task_ref
                else self._latest_task(ledger, identity(session, self.root))
            )
            if not task:
                return None
            raw_key = str(
                payload.get("tool_use_id")
                or payload.get("agent_id")
                or f"{event}:{task['task_ref']}"
            )
            dedupe = f"hook:{event}:{identity(raw_key,self.root)}"
            if dedupe in ledger["dedupe"]:
                return task
            agg = task["aggregate"]
            sanitized = {
                "type": "execution",
                "task_ref": task["task_ref"],
                "route_id": task["route_id"],
                "event": event,
            }
            if event == "PostToolUse":
                agg["tool_count"] += 1
                failed = self._tool_failed(payload.get("tool_response"))
                agg["failure_count"] += int(failed)
                if failed and agg["failure_count"] > 1:
                    agg["retry_count"] += 1
                kind = self._verification_kind(payload)
                if kind and kind not in agg["verification_kinds"]:
                    agg["verification_kinds"].append(kind)
                sanitized.update(
                    tool_kind=self._tool_kind(
                        str(payload.get("tool_name") or "unknown")
                    ),
                    failed=failed,
                    verification_kind=kind,
                )
                transition = self._transition_from_tool(payload)
            elif event in {"SubagentStart", "SubagentStop"}:
                transition = {
                    "phase": "start" if event.endswith("Start") else "stop",
                    "role": self._safe_role(
                        payload.get("agent_type") or payload.get("role")
                    ),
                    "model": self._safe_model(payload.get("model")),
                    "reasoning_effort": self._safe_effort(
                        payload.get("reasoning_effort") or payload.get("effort")
                    ),
                }
            else:
                transition = None
            if transition:
                raw_agent_identity = self._raw_agent_identity(payload)
                if raw_agent_identity is not None:
                    agent_hash = identity(f"agent:{raw_agent_identity}", self.root)
                    transition["stage"] = self._associate_lifecycle_stage(
                        task,
                        transition,
                        agent_hash,
                        int(ledger["next_sequence"]),
                    )
                agg["transitions"].append(transition)
                sanitized["transition"] = transition
            self._append(ledger, sanitized, dedupe)
            _atomic_write_json(ledger_path(self.root), ledger)
            return task

    @staticmethod
    def _count_band(value: int) -> str:
        return (
            "low"
            if value <= 1
            else "medium" if value <= 4 else "high" if value <= 10 else "very_high"
        )

    @staticmethod
    def _duration_band(seconds: float) -> str:
        return (
            "low"
            if seconds < 60
            else (
                "medium" if seconds < 600 else "high" if seconds < 3600 else "very_high"
            )
        )

    @staticmethod
    def _axis_cost(axis: str, value: Any) -> int:
        if axis == "model":
            return MODEL_ORDER.get(str(value), 0)
        if axis == "reasoning_effort":
            return EFFORT_ORDER.get(str(value), 0)
        return 1

    @staticmethod
    def _derive_route_fit(model_fit: str, effort_fit: str, fallback: str) -> str:
        values = {model_fit, effort_fit}
        if "under" in values:
            return "under_routed"
        if "over" in values:
            return "over_routed"
        if "adequate" in values:
            return "adequate"
        return fallback

    @staticmethod
    def _derive_failure_axis(
        route: dict[str, Any],
        replacement: dict[str, Any] | None,
        context_fit: str,
        tool_data_fit: str,
        status: str,
    ) -> str:
        if context_fit == "deficient":
            return "context"
        if tool_data_fit == "deficient":
            return "tool_data"
        if replacement:
            changed = {
                axis
                for axis in ("role", "model", "reasoning_effort")
                if str(route.get(axis)) != str(replacement.get(axis))
            }
            if changed == {"reasoning_effort"}:
                return "reasoning_budget"
            if changed == {"model"}:
                return "model_capability"
            if len(changed) > 1:
                return "confounded"
            if changed:
                return "execution"
        return "execution" if status in {"failed", "corrected"} else "none"

    def _audit_followup_for_task(self, task_ref: str) -> dict[str, Any] | None:
        for event in reversed(_read_jsonl(events_path(self.root))):
            if event.get("task_ref") == task_ref and isinstance(
                event.get("audit_followup"), dict
            ):
                return dict(event["audit_followup"])
        return None

    def finalize_task(
        self,
        task_ref: str,
        *,
        status: str = "completed",
        quality_gate: str = "provisional",
        route_fit: str = "unknown",
        confidence: float = 0.5,
        verified: bool = False,
        verification_kinds: list[str] | None = None,
        replacement_role: str | None = None,
        replacement_model: str | None = None,
        replacement_effort: str | None = None,
        objective_verification: bool = False,
        user_confirmed: bool = False,
        token_band: str = "unknown",
        cost_band: str = "unknown",
        high_risk_regression: bool = False,
        stage: str | None = None,
        model_fit: str = "unknown",
        effort_fit: str = "unknown",
        context_fit: str = "unknown",
        tool_data_fit: str = "unknown",
        failure_axis: str | None = None,
        result_signal: str = "unknown",
        lease_id: str | None = None,
        observed_role: str | None = None,
        observed_model: str | None = None,
        observed_effort: str | None = None,
        observed_execution_target: str | None = None,
        boundary_status: str = "unknown",
        scope_status: str = "unknown",
        archive_status: str | None = None,
        local_input_tokens: int | None = None,
        local_output_tokens: int | None = None,
        local_token_source: str | None = None,
        local_token_complete: bool = False,
    ) -> dict[str, Any]:
        if (
            status not in OUTCOME_STATUSES
            or quality_gate not in QUALITY_GATES
            or route_fit not in ROUTE_FITS
        ):
            raise ValueError("invalid outcome classification")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if token_band not in BANDS or cost_band not in BANDS:
            raise ValueError("invalid resource band")
        if stage is not None and stage not in STAGE_NAMES:
            raise ValueError("invalid outcome stage")
        if model_fit not in MODEL_EFFORT_FITS or effort_fit not in MODEL_EFFORT_FITS:
            raise ValueError("invalid model/effort fit")
        if context_fit not in CONTEXT_TOOL_FITS or tool_data_fit not in CONTEXT_TOOL_FITS:
            raise ValueError("invalid context/tool-data fit")
        if failure_axis is not None and failure_axis not in FAILURE_AXES:
            raise ValueError("invalid failure axis")
        if result_signal not in RESULT_SIGNALS:
            raise ValueError("invalid result signal")
        if boundary_status not in BOUNDARY_STATUSES or scope_status not in SCOPE_STATUSES:
            raise ValueError("invalid boundary/scope status")
        if archive_status is not None and archive_status not in ARCHIVE_STATUSES:
            raise ValueError("invalid archive status")
        supplied_tokens = (local_input_tokens, local_output_tokens, local_token_source)
        if any(value is not None for value in supplied_tokens) and not all(
            value is not None for value in supplied_tokens
        ):
            raise ValueError("exact local input/output tokens and source must be supplied together")
        if local_input_tokens is not None and (
            not isinstance(local_input_tokens, int)
            or isinstance(local_input_tokens, bool)
            or local_input_tokens < 0
            or not isinstance(local_output_tokens, int)
            or isinstance(local_output_tokens, bool)
            or local_output_tokens < 0
            or local_token_source not in {"provider_usage", "codex_usage", "caller_supplied"}
            or local_token_complete is not True
        ):
            raise ValueError("exact local token usage must be complete and source-attributed")
        values = (replacement_role, replacement_model, replacement_effort)
        if any(x is not None for x in values) and not all(
            x is not None for x in values
        ):
            raise ValueError(
                "replacement role, model, and effort must be supplied together"
            )
        with _file_lock(ledger_path(self.root)):
            ledger = self._ledger()
            task = ledger["tasks"].get(task_ref)
            if not task:
                raise ValueError("unknown task_ref")
            route_stages = [
                dict(item)
                for item in task["route"].get("stages", [])
                if isinstance(item, dict) and item.get("required") is True
            ]
            existing_followup = self._audit_followup_for_task(task_ref)
            allowed_stages = {str(item.get("stage")) for item in route_stages}
            if existing_followup:
                allowed_stages.add(str(existing_followup.get("stage")))
            if stage is not None and stage not in allowed_stages:
                raise ValueError(f"stage is not part of task route plan: {stage}")
            stage_source = "caller_supplied" if stage is not None else "unknown"
            attributable = route_stages + ([existing_followup] if existing_followup else [])
            lifecycle_stage = self._recent_completed_lifecycle_stage(task)
            if stage is None and lifecycle_stage in allowed_stages:
                stage = lifecycle_stage
                stage_source = "lifecycle_inferred"
            elif stage is None and len(attributable) == 1:
                stage = str(attributable[0]["stage"])
                stage_source = "single_stage_inferred"
            audit_followup = None
            if (
                result_signal == "exceptional_positive"
                and "audit" not in allowed_stages
            ):
                audit_followup = _stage(
                    load_profile(str(task["route"]["profile"])),
                    "audit",
                    "audit",
                    "router_adversarial_auditor",
                    "xhigh",
                    delegation_depth=int(
                        task["route"].get("delegation_depth") or 0
                    ),
                    execution_target="subagent",
                )
            agg = task["aggregate"]
            lease = None
            if lease_id:
                lease = agg.get("leases", {}).get(lease_id)
                if not isinstance(lease, dict):
                    raise ValueError("unknown stage lease")
            elif isinstance(agg.get("leases"), dict) and agg["leases"]:
                lease = max(
                    agg["leases"].values(),
                    key=lambda item: str(item.get("completed_at") or item.get("started_at") or ""),
                )
                lease_id = str(lease.get("lease_id"))
            if archive_status in {"eligible", "requested", "archived"} and (
                not isinstance(lease, dict)
                or lease.get("archive_status") != archive_status
            ):
                raise ValueError("archive status requires matching observed lease state")
            if (
                stage == "audit"
                and isinstance(existing_followup, dict)
                and (
                    not isinstance(lease, dict)
                    or lease.get("stage_id") != existing_followup.get("stage_id")
                    or lease.get("status") != "completed"
                    or lease.get("quality_gate") != "passed"
                    or lease.get("boundary_status") != "passed"
                    or lease.get("scope_status") != "passed"
                    or lease.get("verification_status") != "passed"
                    or not isinstance(lease.get("observed_execution"), dict)
                )
            ):
                raise ValueError(
                    "dynamic audit outcome requires a completed, fully gated observed audit lease"
                )
            kinds = sorted(
                set((verification_kinds or []) + list(agg["verification_kinds"]))
            )
            if verified and quality_gate == "provisional":
                quality_gate = "passed"
            replacement = (
                {
                    "role": replacement_role,
                    "model": replacement_model,
                    "reasoning_effort": replacement_effort,
                }
                if replacement_role
                else None
            )
            route_fit = self._derive_route_fit(model_fit, effort_fit, route_fit)
            planned_stage = next(
                (item for item in route_stages if item.get("stage") == stage),
                None,
            )
            if planned_stage is None and stage == "audit" and existing_followup:
                planned_stage = dict(existing_followup)
            if (
                isinstance(lease, dict)
                and isinstance(planned_stage, dict)
                and lease.get("stage_id") != planned_stage.get("stage_id")
            ):
                raise ValueError("stage lease does not match the selected planned stage")
            planned = planned_stage or task["route"]
            derived_axis = self._derive_failure_axis(
                planned, replacement, context_fit, tool_data_fit, status
            )
            if failure_axis is not None and derived_axis in {
                "model_capability", "reasoning_budget", "context", "tool_data", "confounded"
            } and failure_axis != derived_axis:
                raise ValueError(
                    f"failure_axis {failure_axis} conflicts with derived {derived_axis}"
                )
            failure_axis = failure_axis or derived_axis
            latest_lifecycle = None
            lifecycles = [
                value for value in agg.get("lifecycle", {}).values()
                if isinstance(value, dict)
                and value.get("status") == "completed"
                and (stage is None or value.get("stage") == stage)
            ]
            if lifecycles:
                latest_lifecycle = max(
                    lifecycles,
                    key=lambda value: int(
                        value.get("completed_sequence") or value.get("started_sequence") or 0
                    ),
                )
            lease_observed = (
                lease.get("observed_execution")
                if isinstance((lease or {}).get("observed_execution"), dict)
                else {}
            )
            explicit_observed = any(
                value is not None
                for value in (
                    observed_role, observed_model, observed_effort,
                    observed_execution_target,
                )
            )
            if explicit_observed and not all(
                value is not None
                for value in (
                    observed_role, observed_model, observed_effort,
                    observed_execution_target,
                )
            ):
                raise ValueError("observed execution provenance must be supplied as a complete tuple")
            observed = {
                "role": str(
                    observed_role
                    or lease_observed.get("role")
                    or (latest_lifecycle or {}).get("role")
                    or "unknown"
                ),
                "model": str(
                    observed_model
                    or lease_observed.get("model")
                    or (latest_lifecycle or {}).get("model")
                    or "unknown"
                ),
                "reasoning_effort": str(
                    observed_effort
                    or lease_observed.get("reasoning_effort")
                    or (latest_lifecycle or {}).get("reasoning_effort")
                    or "unknown"
                ),
                "execution_target": str(
                    observed_execution_target
                    or lease_observed.get("execution_target")
                    or ("subagent" if latest_lifecycle else None)
                    or "unknown"
                ),
            }
            observed["role"] = (
                observed["role"] if observed["role"] in VALID_ROLES else "unknown"
            )
            observed["model"] = (
                observed["model"] if observed["model"] in VALID_MODELS else "unknown"
            )
            observed["reasoning_effort"] = (
                observed["reasoning_effort"]
                if observed["reasoning_effort"] in VALID_EFFORTS else "unknown"
            )
            observed["execution_target"] = (
                observed["execution_target"]
                if observed["execution_target"] in EXECUTION_TARGETS else "unknown"
            )
            comparisons = {
                "role": planned.get("role"),
                "model": planned.get("model"),
                "reasoning_effort": planned.get("reasoning_effort"),
                "execution_target": planned.get("execution_target"),
            }
            complete_observation = all(value != "unknown" for value in observed.values())
            plan_match = (
                "unknown" if not complete_observation
                else "matched" if all(observed[key] == comparisons.get(key) for key in observed)
                else "deviated"
            )
            delegation_depth = int(
                (lease or {}).get("delegation_depth")
                or planned.get("delegation_depth")
                or task["route"].get("delegation_depth")
                or 0
            )
            verification_status = (
                "passed" if quality_gate == "passed"
                else "failed" if quality_gate == "failed"
                else "provisional" if quality_gate == "provisional"
                else "unknown"
            )
            if archive_status is None:
                archive_status = str((lease or {}).get("archive_status") or (
                    "not_ready" if task["route"].get("execution_target") == "visible_task"
                    else "not_applicable"
                ))
            local_tokens = None
            if local_input_tokens is not None and local_output_tokens is not None:
                total_tokens = local_input_tokens + local_output_tokens
                local_tokens = {
                    "input": local_input_tokens,
                    "output": local_output_tokens,
                    "total": total_tokens,
                    "source": local_token_source,
                    "complete": bool(local_token_complete),
                }
                token_band = (
                    "low" if total_tokens < 2000
                    else "medium" if total_tokens < 8000
                    else "high" if total_tokens < 20000
                    else "very_high"
                )
            duration = max(
                0,
                time.time()
                - datetime.fromisoformat(
                    task["started_at"].replace("Z", "+00:00")
                ).timestamp(),
            )
            outcome = {
                "type": "outcome",
                "task_ref": task_ref,
                "route_id": task["route_id"],
                "status": status,
                "quality_gate": quality_gate,
                "route_fit": route_fit,
                "verification_kinds": kinds,
                "confidence": confidence,
                "evidence_source": (
                    "objective" if objective_verification else "hook_heuristic"
                ),
                "objective_verification": bool(objective_verification),
                "user_confirmed": bool(user_confirmed),
                "replacement": replacement,
                "high_risk_regression": bool(high_risk_regression),
                "retry_band": self._count_band(agg["retry_count"]),
                "rework_band": "unknown",
                "tool_band": self._count_band(agg["tool_count"]),
                "duration_band": self._duration_band(duration),
                "token_band": token_band,
                "cost_band": cost_band,
                "model_fit": model_fit,
                "effort_fit": effort_fit,
                "context_fit": context_fit,
                "tool_data_fit": tool_data_fit,
                "failure_axis": failure_axis,
                "result_signal": result_signal,
                "stage_source": stage_source,
                "dispatch_mode": str(task["route"].get("execution_target") or "direct"),
                "observed_execution": observed,
                "plan_match": plan_match,
                "boundary_status": boundary_status,
                "scope_status": scope_status,
                "verification_status": verification_status,
                "archive_status": archive_status,
                "delegation_depth": delegation_depth,
                "stage_lease": {
                    "lease_id": lease_id or "unknown",
                    "status": str((lease or {}).get("status") or "unknown"),
                },
            }
            if local_tokens is not None:
                outcome["local_tokens"] = local_tokens
            if stage is not None:
                outcome["stage"] = stage
            if audit_followup is not None and "audit" not in {
                str(item.get("stage")) for item in route_stages
            }:
                outcome["audit_followup"] = audit_followup
            record = self._append(
                ledger,
                outcome,
                f"outcome:{task_ref}:{status}:{quality_gate}:{route_fit}:{stage or 'all'}:{failure_axis}:{result_signal}",
            )
            task["status"] = "provisional" if quality_gate == "provisional" else status
            task["finalized_at"] = utc_now()
            self._evaluate_shadow(ledger, task, record)
            _atomic_write_json(ledger_path(self.root), ledger)
            return record

    def _evaluate_shadow(
        self, ledger: dict[str, Any], task: dict[str, Any], outcome: dict[str, Any]
    ) -> None:
        shadow = task["route"].get("shadow")
        if not shadow:
            return
        result = "inconclusive"
        axis = shadow.get("axis")
        candidate = shadow.get("candidate")
        replacement = outcome.get("replacement") or {}
        proposal = _proposal_by_id(str(shadow["proposal_id"]), self.root)
        observed = (
            outcome.get("observed_execution")
            if outcome.get("schema_version") == 4
            and isinstance(outcome.get("observed_execution"), dict)
            else task["route"]
        )
        actual = observed.get(axis)
        fixed_context = {
            "role": observed.get("role"),
            "model": observed.get("model"),
            "reasoning_effort": observed.get("reasoning_effort"),
            "execution_target": observed.get("execution_target", "legacy"),
            "delegation_depth": outcome.get(
                "delegation_depth", task["route"].get("delegation_depth", "legacy")
            ),
            "stage": outcome.get("stage", "legacy"),
        }
        fixed_mismatch = isinstance(proposal.get("fixed"), dict) and any(
            str(fixed_context.get(key)) != str(value)
            for key, value in proposal["fixed"].items()
        )
        route = task["route"]
        primary_candidates = [
            stage
            for stage in route.get("stages") or []
            if all(
                str(stage.get(key)) == str(route.get(key))
                for key in (
                    "role",
                    "model",
                    "reasoning_effort",
                    "execution_target",
                )
            )
        ]
        primary_stage = primary_candidates[0] if len(primary_candidates) == 1 else None
        lease_summary = outcome.get("stage_lease")
        lease = None
        if isinstance(lease_summary, dict):
            lease = task.get("aggregate", {}).get("leases", {}).get(
                lease_summary.get("lease_id")
            )
        delegated_execution_invalid = (
            route.get("execution_target") != "direct"
            and (
                not isinstance(primary_stage, dict)
                or outcome.get("stage") != primary_stage.get("stage")
                or not isinstance(lease, dict)
                or lease.get("status") != "completed"
                or lease.get("stage_id") != primary_stage.get("stage_id")
                or not isinstance(lease.get("observed_execution"), dict)
            )
        )
        learning = load_policy(self.root)["learning"]
        if (
            fixed_mismatch
            or route.get("dispatch_ready") is False
            or outcome.get("schema_version") == 4
            and (
                not outcome.get("objective_verification")
                or not outcome.get("user_confirmed")
                or outcome.get("status") not in {"escalated", "overridden"}
                or outcome.get("quality_gate") not in {"passed", "failed"}
                or float(outcome.get("confidence") or 0)
                < learning["minimum_confidence"]
                or outcome.get("plan_match") != "matched"
                or outcome.get("boundary_status") != "passed"
                or outcome.get("scope_status") != "passed"
                or outcome.get("verification_status") not in {"passed", "failed"}
                or outcome.get("context_fit") != "adequate"
                or outcome.get("tool_data_fit") != "adequate"
                or not isinstance(primary_stage, dict)
                or outcome.get("stage")
                not in {None, primary_stage.get("stage")}
                or delegated_execution_invalid
            )
            or outcome.get("failure_axis") in {"confounded", "context", "tool_data"}
            or outcome.get("context_fit") == "deficient"
            or outcome.get("tool_data_fit") == "deficient"
        ):
            result = "inconclusive"
        elif (
            outcome.get("route_fit") in {"under_routed", "over_routed"}
            and replacement.get(axis) == candidate
        ):
            result = "candidate_win"
        elif outcome.get("route_fit") == "adequate":
            if self._axis_cost(axis, candidate) > self._axis_cost(axis, actual):
                result = "incumbent_win"
            elif self._axis_cost(axis, candidate) == self._axis_cost(axis, actual):
                result = "tie"
        self._record_shadow_result(shadow["proposal_id"], result, task.get("project"))

    def _record_shadow_result(
        self, proposal_id: str, result: str, project: str | None
    ) -> None:
        with _file_lock(state_lock_path(self.root)):
            shadows = load_shadows(self.root)
            item = shadows["items"].get(proposal_id)
            if not isinstance(item, dict) or item.get("state") != "active":
                return
            item.setdefault("observations", []).append(
                {"result": result, "project": project, "created_at": utc_now(), "source": "task_outcome"}
            )
            comparable = [
                x
                for x in item["observations"]
                if x["result"] != "inconclusive" and x.get("source") == "task_outcome"
            ]
            support = sum(x["result"] == "candidate_win" for x in comparable)
            losses = sum(x["result"] == "incumbent_win" for x in comparable)
            projects = {x.get("project") for x in comparable if x.get("project")}
            learning = load_policy(self.root)["learning"]
            if (
                len(comparable) >= learning["shadow_minimum_comparable"]
                and support >= learning["shadow_minimum_support"]
                and losses <= learning["shadow_maximum_losses"]
                and not item.get("high_risk_regression")
                and (
                    item["proposal"].get("scope") != "global"
                    or len(projects) >= learning["minimum_distinct_projects_for_global"]
                )
            ):
                item["state"] = "validated"
                item["validated_at"] = utc_now()
            _atomic_write_json(shadows_path(self.root), shadows)

    def evaluate_policy(self) -> list[dict[str, Any]]:
        return learning_proposals(self.root)

    def status(self) -> dict[str, Any]:
        return policy_status(self.root)


def _all_events(root: Path | None = None) -> list[dict[str, Any]]:
    return _read_jsonl(events_path(root))


def _events_by_type(
    root: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    routes = {}
    outcomes = []
    for record in _all_events(root):
        if record.get("type") == "route" and record.get("route_id"):
            routes[str(record["route_id"])] = record
        elif record.get("type") == "outcome":
            outcomes.append(record)
    return routes, outcomes


def create_route_record(
    plan: RoutePlan,
    task: str,
    *,
    session_id: str | None = None,
    project_fingerprint: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    engine = RouterEngine(root)
    with _file_lock(ledger_path(engine.root)):
        ledger = engine._ledger()
        reference = identity(f"manual:{plan.route_id}", engine.root)
        route = engine._route_payload(plan)
        task_record = {
            "task_ref": reference,
            "route_id": plan.route_id,
            "session": identity(session_id or "manual", engine.root),
            "turn": identity(plan.route_id, engine.root),
            "project": identity(project_fingerprint or "unspecified", engine.root),
            "status": "active",
            "started_at": utc_now(),
            "started_sequence": int(ledger["next_sequence"]),
            "route": route,
            "aggregate": {
                "tool_count": 0,
                "failure_count": 0,
                "retry_count": 0,
                "verification_kinds": [],
                "transitions": [],
                "lifecycle": {},
                "leases": {},
            },
        }
        ledger["tasks"][reference] = task_record
        record = engine._append(
            ledger,
            {
                "type": "route",
                "task_ref": reference,
                "task_fingerprint": identity(task, engine.root),
                **route,
                "session": task_record["session"],
                "project": task_record["project"],
            },
            f"route:{reference}",
        )
        _atomic_write_json(ledger_path(engine.root), ledger)
        return record


def record_outcome(
    route_id: str,
    status: str,
    *,
    confidence: float,
    replacement_role: str | None = None,
    replacement_model: str | None = None,
    replacement_effort: str | None = None,
    verified: bool = False,
    quality_gate: str | None = None,
    route_fit: str = "unknown",
    verification_kinds: list[str] | None = None,
    objective_verification: bool = False,
    user_confirmed: bool = False,
    token_band: str = "unknown",
    cost_band: str = "unknown",
    high_risk_regression: bool = False,
    stage: str | None = None,
    model_fit: str = "unknown",
    effort_fit: str = "unknown",
    context_fit: str = "unknown",
    tool_data_fit: str = "unknown",
    failure_axis: str | None = None,
    result_signal: str = "unknown",
    lease_id: str | None = None,
    observed_role: str | None = None,
    observed_model: str | None = None,
    observed_effort: str | None = None,
    observed_execution_target: str | None = None,
    boundary_status: str = "unknown",
    scope_status: str = "unknown",
    archive_status: str | None = None,
    local_input_tokens: int | None = None,
    local_output_tokens: int | None = None,
    local_token_source: str | None = None,
    local_token_complete: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    routes, _ = _events_by_type(root)
    route = routes.get(route_id)
    if not route:
        raise ValueError("unknown route_id")
    return RouterEngine(root).finalize_task(
        str(route["task_ref"]),
        status=status,
        quality_gate=quality_gate or ("passed" if verified else "provisional"),
        route_fit=route_fit,
        confidence=confidence,
        verified=verified,
        verification_kinds=verification_kinds,
        replacement_role=replacement_role,
        replacement_model=replacement_model,
        replacement_effort=replacement_effort,
        objective_verification=objective_verification,
        user_confirmed=user_confirmed,
        token_band=token_band,
        cost_band=cost_band,
        high_risk_regression=high_risk_regression,
        stage=stage,
        model_fit=model_fit,
        effort_fit=effort_fit,
        context_fit=context_fit,
        tool_data_fit=tool_data_fit,
        failure_axis=failure_axis,
        result_signal=result_signal,
        lease_id=lease_id,
        observed_role=observed_role,
        observed_model=observed_model,
        observed_effort=observed_effort,
        observed_execution_target=observed_execution_target,
        boundary_status=boundary_status,
        scope_status=scope_status,
        archive_status=archive_status,
        local_input_tokens=local_input_tokens,
        local_output_tokens=local_output_tokens,
        local_token_source=local_token_source,
        local_token_complete=local_token_complete,
    )


def _axis_direction(axis: str, before: str, after: str) -> str:
    if axis == "role":
        return "reroute"
    a, b = RouterEngine._axis_cost(axis, before), RouterEngine._axis_cost(axis, after)
    return "upgrade" if b > a else "downgrade" if b < a else "reroute"


def learning_proposals(root: Path | None = None) -> list[dict[str, Any]]:
    routes, outcomes = _events_by_type(root)
    learning = load_policy(root)["learning"]
    grouped = {}
    for outcome in outcomes:
        route = routes.get(str(outcome.get("route_id")))
        replacement = outcome.get("replacement")
        if (
            not route
            or not isinstance(replacement, dict)
            or not outcome.get("objective_verification")
            or not outcome.get("user_confirmed")
            or outcome.get("status") not in {"escalated", "overridden"}
            or outcome.get("quality_gate") not in {"passed", "failed"}
            or route.get("dispatch_ready") is False
            or float(outcome.get("confidence") or 0) < learning["minimum_confidence"]
            or (
                outcome.get("schema_version") == 4
                and (
                    outcome.get("plan_match") != "matched"
                    or outcome.get("boundary_status") != "passed"
                    or outcome.get("scope_status") != "passed"
                    or outcome.get("verification_status") not in {"passed", "failed"}
                    or outcome.get("context_fit") != "adequate"
                    or outcome.get("tool_data_fit") != "adequate"
                )
            )
        ):
            continue
        observed = (
            outcome.get("observed_execution")
            if outcome.get("schema_version") == 4
            and isinstance(outcome.get("observed_execution"), dict)
            else route
        )
        primary_candidates = [
            stage for stage in route.get("stages") or []
            if all(
                str(stage.get(key)) == str(route.get(key))
                for key in ("role", "model", "reasoning_effort", "execution_target")
            )
        ]
        if outcome.get("schema_version") == 4:
            if len(primary_candidates) != 1:
                continue
            primary_stage = primary_candidates[0]
            if outcome.get("stage") not in {None, primary_stage.get("stage")}:
                continue
            if route.get("execution_target") != "direct":
                lease = outcome.get("stage_lease")
                if (
                    outcome.get("stage") != primary_stage.get("stage")
                    or not isinstance(lease, dict)
                    or lease.get("lease_id") == "unknown"
                    or lease.get("status") != "completed"
                ):
                    continue
        else:
            primary_stage = primary_candidates[0] if len(primary_candidates) == 1 else None
        changed_axes = {
            axis
            for axis in ("role", "model", "reasoning_effort")
            if str(observed.get(axis)) != str(replacement.get(axis))
        }
        for axis in ("model", "reasoning_effort"):
            before, after = str(observed.get(axis)), str(replacement.get(axis))
            if before == after:
                continue
            if axis == "model" and changed_axes != {"model"}:
                continue
            if axis == "reasoning_effort" and changed_axes != {"reasoning_effort"}:
                continue
            failure_axis = outcome.get("failure_axis")
            if failure_axis in {"confounded", "context", "tool_data"}:
                continue
            if outcome.get("context_fit") == "deficient" or outcome.get("tool_data_fit") == "deficient":
                continue
            if axis == "model" and failure_axis not in {None, "model_capability", "none"}:
                continue
            if axis == "reasoning_effort" and failure_axis not in {None, "reasoning_budget", "none"}:
                continue
            profile_config = load_profile(str(route.get("profile")))
            try:
                if axis == "model":
                    _validate_route_tuple(
                        profile_config,
                        str(observed.get("role")),
                        after,
                        str(observed.get("reasoning_effort")),
                    )
                else:
                    _validate_route_tuple(
                        profile_config,
                        str(observed.get("role")),
                        str(observed.get("model")),
                        after,
                    )
            except ValueError:
                continue
            fixed = {
                "role": str(observed.get("role")),
                "execution_target": str(observed.get("execution_target", "legacy")),
                "delegation_depth": str(
                    outcome.get("delegation_depth", route.get("delegation_depth", "legacy"))
                ),
                "stage": str(
                    primary_stage.get("stage") if isinstance(primary_stage, dict) else "legacy"
                ),
            }
            fixed[
                "reasoning_effort" if axis == "model" else "model"
            ] = str(
                observed.get("reasoning_effort" if axis == "model" else "model")
            )
            fixed_covariates = [
                f"{name}={fixed[name]}" for name in sorted(fixed)
            ]
            key = "|".join(
                [
                    str(route.get("profile")),
                    str(route.get("task_class")),
                    str(outcome.get("dispatch_mode", route.get("execution_target", "legacy"))),
                    str(outcome.get("delegation_depth", route.get("delegation_depth", "legacy"))),
                    *fixed_covariates,
                    axis,
                    before,
                    after,
                ]
            )
            grouped.setdefault(key, []).append(
                (route, outcome, axis, before, after, fixed)
            )
    result = []
    for key, items in grouped.items():
        sessions = {str(x[0].get("session")) for x in items}
        projects = {str(x[0].get("project")) for x in items}
        confidence = sum(float(x[1]["confidence"]) for x in items) / len(items)
        route, _, axis, before, after, fixed = items[-1]
        regression = any(x[1].get("high_risk_regression") for x in items)
        eligible = (
            len(items) >= learning["minimum_replacement_outcomes"]
            and len(sessions) >= learning["minimum_independent_sessions"]
            and confidence >= learning["minimum_confidence"]
            and not regression
        )
        scope = (
            "global"
            if len(projects) >= learning["minimum_distinct_projects_for_global"]
            else "repository"
        )
        proposal_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        shadow = load_shadows(root)["items"].get(proposal_id, {})
        status = "ready_for_shadow" if eligible else "collecting_evidence"
        if shadow.get("state") == "active":
            status = "shadow_running"
        elif shadow.get("state") == "validated":
            status = "ready_for_confirmation"
        elif shadow.get("state") == "rejected":
            status = "rejected"
        result.append(
            {
                "proposal_id": proposal_id,
                "axis": axis,
                "scope": scope,
                "status": status,
                "direction": _axis_direction(axis, before, after),
                "profile": route.get("profile"),
                "task_class": route.get("task_class"),
                "stage": fixed["stage"],
                "fixed": fixed,
                "from": before,
                "to": after,
                "replacement_outcomes": len(items),
                "independent_sessions": len(sessions),
                "distinct_projects": len(projects),
                "confidence": round(confidence, 3),
                "high_risk_regression": regression,
            }
        )
    return sorted(
        result,
        key=lambda x: (x["status"], -x["replacement_outcomes"], x["proposal_id"]),
    )


def _proposal_by_id(proposal_id: str, root: Path | None = None) -> dict[str, Any]:
    value = next(
        (x for x in learning_proposals(root) if x["proposal_id"] == proposal_id), None
    )
    if not value:
        raise ValueError("proposal_id has no eligible routing evidence")
    return value


def start_shadow(proposal_id: str, root: Path | None = None) -> dict[str, Any]:
    proposal = _proposal_by_id(proposal_id, root)
    if proposal["status"] != "ready_for_shadow":
        raise ValueError("proposal must be ready_for_shadow")
    with _file_lock(state_lock_path(root)):
        shadows = load_shadows(root)
        shadows["items"][proposal_id] = {
            "state": "active",
            "started_at": utc_now(),
            "proposal": proposal,
            "observations": [],
            "high_risk_regression": False,
        }
        _atomic_write_json(shadows_path(root), shadows)
        return dict(shadows["items"][proposal_id])


def record_shadow_observation(
    proposal_id: str, success: bool, root: Path | None = None
) -> dict[str, Any]:
    """Keep the legacy boolean wire shape without treating it as policy evidence.

    A manual success bit has no executed candidate, objective verification, or
    loss context, so it must remain inconclusive and cannot advance readiness.
    """
    if type(success) is not bool:
        raise ValueError("success must be boolean")
    with _file_lock(state_lock_path(root)):
        shadows = load_shadows(root)
        item = shadows["items"].get(proposal_id)
        if not isinstance(item, dict) or item.get("state") != "active":
            raise ValueError("proposal is not active")
        item.setdefault("observations", []).append(
            {
                "result": "inconclusive",
                "project": None,
                "created_at": utc_now(),
                "source": "legacy_manual_boolean",
            }
        )
        _atomic_write_json(shadows_path(root), shadows)
        return dict(item)


def confirm_policy_change(
    proposal_id: str, confirmed_by_user: bool, root: Path | None = None
) -> dict[str, Any]:
    if not confirmed_by_user:
        raise ValueError("policy changes require explicit user confirmation")
    proposal = _proposal_by_id(proposal_id, root)
    with _file_lock(state_lock_path(root)):
        shadows = load_shadows(root)
        shadow = shadows["items"].get(proposal_id)
        if proposal["status"] != "ready_for_confirmation" or not isinstance(shadow, dict):
            raise ValueError("proposal requires successful shadow validation")
        policy = load_policy(root)
        override = {
            "proposal_id": proposal_id,
            "profile": proposal["profile"],
            "task_class": proposal["task_class"],
            "stage": proposal["stage"],
            "fixed": dict(proposal["fixed"]),
            "axis": proposal["axis"],
            "to": proposal["to"],
            "scope": proposal["scope"],
            "confirmed_at": utc_now(),
        }
        policy["overrides"] = [
            x
            for x in policy["overrides"]
            if not (
                x.get("profile") == override["profile"]
                and x.get("task_class") == override["task_class"]
                and x.get("axis") == override["axis"]
                and x.get("fixed") == override["fixed"]
            )
        ] + [override]
        policy["revision"] = int(policy.get("revision") or 1) + 1
        policy["schema_version"] = 2
        policy["updated_at"] = utc_now()
        _atomic_write_json(policy_path(root), policy)
        shadow["state"] = "confirmed"
        shadow["confirmed_at"] = utc_now()
        _atomic_write_json(shadows_path(root), shadows)
        return override


def _wilson(successes: int, total: int) -> dict[str, float | None]:
    if total == 0:
        return {"low": None, "high": None}
    z = 1.959963984540054
    p = successes / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / den
    return {
        "low": round(max(0, centre - half), 4),
        "high": round(min(1, centre + half), 4),
    }


def router_metrics(root: Path | None = None) -> dict[str, Any]:
    routes, outcomes = _events_by_type(root)
    latest = {}
    for outcome in outcomes:
        latest[str(outcome.get("route_id"))] = outcome
    known = [
        x for x in latest.values() if x.get("quality_gate") in {"passed", "failed"}
    ]
    success = sum(x.get("quality_gate") == "passed" for x in known)
    total = len(known)
    correction = sum(x.get("status") == "corrected" for x in latest.values())
    escalation = sum(x.get("status") == "escalated" for x in latest.values())
    under = sum(x.get("route_fit") == "under_routed" for x in known)
    over = sum(x.get("route_fit") == "over_routed" for x in known)

    def actual_model_effort(outcome: dict[str, Any]) -> tuple[str, str]:
        route = routes.get(str(outcome.get("route_id")), {})
        if outcome.get("schema_version") != 4:
            return (
                str(route.get("model") or "unknown"),
                str(route.get("reasoning_effort") or "unknown"),
            )
        observed = outcome.get("observed_execution")
        if not isinstance(observed, dict) or not all(
            observed.get(key) != "unknown"
            for key in ("role", "model", "reasoning_effort", "execution_target")
        ):
            return "unknown", "unknown"
        return str(observed["model"]), str(observed["reasoning_effort"])

    unnecessary = sum(
        x.get("route_fit") == "over_routed"
        and (
            actual_model_effort(x)[0] == "gpt-5.6-sol"
            or actual_model_effort(x)[1] == "xhigh"
        )
        for x in known
    )
    brier = []
    buckets = {
        f"{i/10:.1f}-{(i+1)/10:.1f}": {"count": 0, "predicted": 0.0, "observed": 0.0}
        for i in range(10)
    }
    for route_id, outcome in latest.items():
        if outcome not in known or route_id not in routes:
            continue
        predicted = float(routes[route_id].get("confidence") or 0.5)
        observed = 1.0 if outcome.get("quality_gate") == "passed" else 0.0
        brier.append((predicted - observed) ** 2)
        key = list(buckets)[min(9, int(predicted * 10))]
        item = buckets[key]
        item["count"] += 1
        item["predicted"] += predicted
        item["observed"] += observed
    for item in buckets.values():
        if item["count"]:
            item["predicted"] = round(item["predicted"] / item["count"], 3)
            item["observed"] = round(item["observed"] / item["count"], 3)
    comparable = [x for x in known if x.get("quality_gate") == "passed"]
    resources = {
        band: {
            kind: sum(x.get(f"{kind}_band") == band for x in comparable)
            for kind in ("duration", "tool", "token", "cost")
        }
        for band in BANDS
    }
    success_by_tuple: dict[str, dict[str, Any]] = {}
    interaction: dict[str, int] = {}
    for route_id, outcome in latest.items():
        route = routes.get(route_id)
        if not route or outcome.get("quality_gate") not in {"passed", "failed"}:
            continue
        actual_model, actual_effort = actual_model_effort(outcome)
        key = "|".join(
            (str(route.get("task_class") or "unknown"), actual_model, actual_effort)
        )
        item = success_by_tuple.setdefault(key, {"passed": 0, "total": 0, "rate": None})
        item["total"] += 1
        item["passed"] += int(outcome.get("quality_gate") == "passed")
        interaction["|".join(key.split("|")[1:])] = interaction.get(
            "|".join(key.split("|")[1:]), 0
        ) + 1
    for item in success_by_tuple.values():
        item["rate"] = round(item["passed"] / item["total"], 4)
    model_fit_counts = {
        fit: sum(outcome.get("model_fit") == fit for outcome in latest.values())
        for fit in MODEL_EFFORT_FITS
    }
    effort_fit_counts = {
        fit: sum(outcome.get("effort_fit") == fit for outcome in latest.values())
        for fit in MODEL_EFFORT_FITS
    }
    model_fit_denominator = sum(
        model_fit_counts[fit] for fit in MODEL_EFFORT_FITS if fit != "unknown"
    )
    effort_fit_denominator = sum(
        effort_fit_counts[fit] for fit in MODEL_EFFORT_FITS if fit != "unknown"
    )
    floor_violations = 0
    decision_leakage = 0
    mechanical = []
    stage_outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    audit_followups: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        route_id = str(outcome.get("route_id"))
        if isinstance(outcome.get("stage"), str):
            stage_outcomes[(route_id, str(outcome["stage"]))] = outcome
        if isinstance(outcome.get("audit_followup"), dict):
            audit_followups[route_id] = outcome["audit_followup"]
    handoff_numerator = 0
    handoff_denominator = 0
    for route_id, route in routes.items():
        floor = route.get("capability_floor")
        if floor in MODEL_ORDER and MODEL_ORDER.get(str(route.get("model")), 0) < MODEL_ORDER[floor]:
            floor_violations += 1
        for stage in route.get("stages") or []:
            authority = stage.get("authority")
            expected = AUTHORITY_FLOORS.get(str(authority))
            if expected and MODEL_ORDER.get(str(stage.get("model")), 0) < MODEL_ORDER[expected]:
                floor_violations += 1
            if authority in {"decision", "audit"} and stage.get("model") != "gpt-5.6-sol":
                decision_leakage += 1
        features = route.get("decision_features") or {}
        if features.get("cognitive_type") == "direct" and features.get("scope") == "tiny":
            mechanical.append(route)
        required_stages = [
            str(stage.get("stage"))
            for stage in route.get("stages") or []
            if stage.get("required") is True
        ]
        followup = audit_followups.get(route_id)
        if followup and followup.get("required") is True and "audit" not in required_stages:
            required_stages.append("audit")
        for left_name, right_name in pairwise(required_stages):
            left = stage_outcomes.get((route_id, left_name))
            right = stage_outcomes.get((route_id, right_name))
            if not left or not right:
                continue
            if not left.get("objective_verification") or not right.get("objective_verification"):
                continue
            if left.get("quality_gate") not in {"passed", "failed"} or right.get("quality_gate") not in {"passed", "failed"}:
                continue
            handoff_denominator += 1
            handoff_numerator += int(
                left.get("quality_gate") == "passed"
                and right.get("quality_gate") == "passed"
            )
    return {
        "schema_version": 4,
        "release_version": "1.3.0",
        "capture_coverage": round(len(latest) / len(routes), 4) if routes else 0.0,
        "known_quality_coverage": round(total / len(routes), 4) if routes else 0.0,
        "route_success": round(success / total, 4) if total else None,
        "route_success_wilson_95": _wilson(success, total),
        "correction_rate": round(correction / len(routes), 4) if routes else 0.0,
        "escalation_rate": round(escalation / len(routes), 4) if routes else 0.0,
        "under_routing_rate": round(under / total, 4) if total else None,
        "over_routing_rate": round(over / total, 4) if total else None,
        "unnecessary_sol_xhigh": unnecessary,
        "brier_score": round(sum(brier) / len(brier), 4) if brier else None,
        "calibration_buckets": buckets,
        "equal_quality_resource_bands": resources,
        "task_class_model_effort_success": success_by_tuple,
        "model_fit_counts": model_fit_counts,
        "effort_fit_counts": effort_fit_counts,
        "model_fit_denominator": model_fit_denominator,
        "model_under_routing_rate": (
            round(model_fit_counts["under"] / model_fit_denominator, 4)
            if model_fit_denominator
            else None
        ),
        "model_over_routing_rate": (
            round(model_fit_counts["over"] / model_fit_denominator, 4)
            if model_fit_denominator
            else None
        ),
        "effort_fit_denominator": effort_fit_denominator,
        "effort_under_routing_rate": (
            round(effort_fit_counts["under"] / effort_fit_denominator, 4)
            if effort_fit_denominator
            else None
        ),
        "effort_over_routing_rate": (
            round(effort_fit_counts["over"] / effort_fit_denominator, 4)
            if effort_fit_denominator
            else None
        ),
        "floor_violations": floor_violations,
        "decision_leakage": decision_leakage,
        "mechanical_sol_share": (
            round(sum(item.get("model") == "gpt-5.6-sol" for item in mechanical) / len(mechanical), 4)
            if mechanical
            else None
        ),
        "stage_handoff_success": {
            "numerator": handoff_numerator,
            "denominator": handoff_denominator,
            "rate": (
                round(handoff_numerator / handoff_denominator, 4)
                if handoff_denominator
                else None
            ),
            "passed": handoff_numerator,
            "total": handoff_denominator,
        },
        "quality_adjusted_resource_bands": resources,
        "model_effort_interaction_comparable": interaction,
        "routes": len(routes),
        "outcomes": len(outcomes),
    }


def gardener_candidates(root: Path | None = None) -> list[dict[str, Any]]:
    return [
        {
            "proposal_id": x["proposal_id"],
            "knowledge_scope": x["scope"],
            "scope": f"adaptive routing: {x['profile']} {x['task_class']}",
            "lesson": f"Change {x['axis']} from {x['from']} to {x['to']} only after shadow validation.",
            "evidence": f"{x['replacement_outcomes']} high-quality replacement outcomes across {x['independent_sessions']} sessions.",
            "recommended_target": "skill",
            "confidence": x["confidence"],
            "status": x["status"],
        }
        for x in learning_proposals(root)
        if x["status"] in {"ready_for_shadow", "ready_for_confirmation"}
    ]


def policy_status(root: Path | None = None) -> dict[str, Any]:
    policy = load_policy(root)
    routes, outcomes = _events_by_type(root)
    shadows = load_shadows(root)
    metrics = router_metrics(root)
    return {
        "policy_revision": policy["revision"],
        "profiles": available_profiles(),
        "stored_routes": len(routes),
        "stored_outcomes": len(outcomes),
        "active_overrides": len(policy["overrides"]),
        "coverage": {k: v for k, v in metrics.items() if "coverage" in k},
        "metrics": metrics,
        "shadow": {
            "active": sum(
                x.get("state") == "active" for x in shadows["items"].values()
            ),
            "ready_for_confirmation": sum(
                x.get("state") == "validated" for x in shadows["items"].values()
            ),
        },
        "proposals": learning_proposals(root),
        "gardener_candidates": gardener_candidates(root),
    }


def hook_context(
    event: str, payload: dict[str, Any], root: Path | None = None
) -> dict[str, Any] | None:
    engine = RouterEngine(root)
    if event == "SessionStart":
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": "Codex Adaptive Router v1.3.0 is active; Thin Root capability/quality floors, recursive dispatch leases, and human-confirmed policy changes are enforced.",
            }
        }
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return None
        task = engine.begin_task(
            session_id=str(payload.get("session_id") or "unknown"),
            turn_id=str(payload.get("turn_id") or uuid.uuid4()),
            prompt=prompt,
            project=str(payload.get("cwd") or "unspecified"),
        )
        route = task["route"]
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": f"Adaptive Router task_ref={task['task_ref']}; route={route['role']}; model={route['model']}; effort={route['reasoning_effort']}; target={route['execution_target']}; dispatch={'ready' if route['dispatch_ready'] else route['dispatch_blocker']}. Confirm this immutable Route Plan v3 with route_plan. Claim delegated stages before work; never bypass a blocker; unresolved semantics escalate to Sol.",
            }
        }
    if event == "SubagentStart":
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": "Stay within the assigned role and escalate unresolved high-impact conclusions to Sol.",
            }
        }
    return None


def record_hook_event(
    event: str, payload: dict[str, Any], root: Path | None = None
) -> None:
    engine = RouterEngine(root)
    if event == "UserPromptSubmit":
        return
    if event in {"PostToolUse", "SubagentStart", "SubagentStop"}:
        engine.observe_event(event, payload)
        return
    if event == "Stop":
        session, turn = str(payload.get("session_id") or "unknown"), str(
            payload.get("turn_id") or ""
        )
        if turn:
            with contextlib.suppress(ValueError):
                engine.finalize_task(
                    identity(f"task:{session}:{turn}", engine.root),
                    status="completed",
                    quality_gate="provisional",
                    confidence=0.5,
                )
    elif event == "SessionEnd":
        with _file_lock(ledger_path(engine.root)):
            ledger = engine._ledger()
            session_hash = identity(
                str(payload.get("session_id") or "unknown"), engine.root
            )
            pending = [
                task["task_ref"]
                for task in ledger["tasks"].values()
                if task.get("session") == session_hash
                and task.get("status") == "active"
            ]
        for reference in pending:
            with contextlib.suppress(ValueError):
                engine.finalize_task(
                    reference,
                    status="completed",
                    quality_gate="provisional",
                    confidence=0.4,
                )
