"""Deterministic, local policy engine for Codex Adaptive Router.

The engine deliberately does not send prompts, source code, paths, or tool output
anywhere. Persistent records contain only hashes, routing metadata, and explicit
outcome labels supplied by Codex or the user.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
VALID_MODELS = {"gpt-5.6", "gpt-5.6-terra", "gpt-5.6-luna"}
VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
OUTCOME_STATUSES = {"completed", "verified", "failed", "corrected", "escalated", "overridden"}
TASK_STATES = {"unknown", "frozen"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]


def plugin_data_root() -> Path:
    explicit = os.environ.get("CODEX_ADAPTIVE_ROUTER_DATA") or os.environ.get("PLUGIN_DATA")
    if explicit:
        return Path(explicit).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return codex_home / "codex-adaptive-router"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return []
    return records


@contextlib.contextmanager
def _file_lock(path: Path, timeout_seconds: float = 3.0) -> Iterator[None]:
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"router storage is busy: {path.name}")
            time.sleep(0.04)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            lock.unlink()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _profile_path(name: str) -> Path:
    return PLUGIN_ROOT / "profiles" / f"{name}.json"


def available_profiles() -> list[str]:
    return sorted(path.stem for path in (PLUGIN_ROOT / "profiles").glob("*.json"))


def load_profile(name: str) -> dict[str, Any]:
    selected = name if _profile_path(name).is_file() else "generic"
    profile = _read_json(_profile_path(selected), {})
    if not isinstance(profile, dict) or not isinstance(profile.get("roles"), dict):
        raise ValueError(f"invalid router profile: {selected}")
    return profile


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "revision": 1,
        "updated_at": utc_now(),
        "learning": {
            "minimum_independent_sessions": 3,
            "minimum_distinct_projects_for_global": 2,
            "minimum_confidence": 0.85,
            "shadow_successes_required": 5,
        },
        "overrides": [],
    }


def policy_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "policy" / "current.json"


def load_policy(root: Path | None = None) -> dict[str, Any]:
    policy = _read_json(policy_path(root), None)
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        return default_policy()
    policy.setdefault("overrides", [])
    policy.setdefault("learning", default_policy()["learning"])
    return policy


def save_policy(policy: dict[str, Any], root: Path | None = None) -> None:
    policy["updated_at"] = utc_now()
    _atomic_write_json(policy_path(root), policy)


def events_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "events" / "routing.jsonl"


def shadows_path(root: Path | None = None) -> Path:
    return (root or plugin_data_root()) / "learning" / "shadows.json"


def load_shadows(root: Path | None = None) -> dict[str, Any]:
    value = _read_json(shadows_path(root), {"schema_version": 1, "items": {}})
    if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
        return {"schema_version": 1, "items": {}}
    return value


def save_shadows(value: dict[str, Any], root: Path | None = None) -> None:
    _atomic_write_json(shadows_path(root), value)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


QUANT_TERMS = (
    "quant", "strategy", "backtest", "sharpe", "alpha", "regime", "factor", "回测", "策略", "量化", "夏普", "因子", "市场状态", "换月", "涨跌停",
)
MAPPER_TERMS = (
    "find ", "locate", "where is", "search code", "call chain", "reference", "grep", "rg ", "实现位置", "调用链", "搜索代码", "定位文件", "引用搜索",
)
RUNNER_TERMS = (
    "batch", "parameter sweep", "run tests", "run test", "benchmark", "collect metrics", "500 ", "1000 ", "批量", "参数扫描", "跑测试", "运行测试", "整理结果", "收集指标",
)
IMPLEMENTATION_TERMS = (
    "implement", "add ", "build ", "refactor", "fix ", "write code", "实现", "新增", "重构", "修复", "写代码", "增加",
)
ARCHITECTURE_TERMS = (
    "architecture", "semantic", "data model", "trade-off", "system design", "time semantics", "accounting", "design the", "架构", "语义", "数据模型", "系统设计", "权衡", "撮合", "资金账户",
)
RESEARCH_TERMS = (
    "why", "root cause", "diagnose", "hypothesis", "attribution", "statistical", "regression", "research", "原因", "根因", "诊断", "假说", "归因", "统计", "研究", "为什么",
)
AUDIT_TERMS = (
    "audit", "credible", "too good", "leakage", "overfit", "adversarial", "review result", "sharpe 3", "审计", "可信", "异常好", "数据泄漏", "过拟合", "反证", "审查结果",
)
SCOUT_TERMS = (
    "new strategy", "new direction", "novel", "brainstorm", "stuck", "local optimum", "全新策略", "新方向", "发散", "局部最优", "创新",
)
SIMPLE_TERMS = ("translate", "rewrite", "rename", "explain this line", "翻译", "改写", "一句", "命名")


def infer_profile(task: str, requested: str | None = None) -> str:
    if requested in available_profiles():
        return str(requested)
    return "quant" if _contains(_normalise(task), QUANT_TERMS) else "generic"


def _task_class_and_role(task: str, profile: str, task_state: str, force_role: str | None) -> tuple[str, str, list[str], float]:
    text = _normalise(task)
    if force_role:
        return "forced", force_role, ["explicit role override"], 1.0
    if _contains(text, MAPPER_TERMS):
        return "discovery", "router_code_mapper", ["bounded code or evidence discovery"], 0.9
    if _contains(text, RUNNER_TERMS):
        return "execution", "router_experiment_runner", ["defined batch, test, or metric collection work"], 0.88
    if _contains(text, AUDIT_TERMS):
        return "audit", "router_adversarial_auditor", ["adversarial review or unusually strong result"], 0.95
    if _contains(text, SCOUT_TERMS):
        return "exploration", "router_strategy_scout", ["open-ended discovery or local-optimum escape"], 0.93
    if _contains(text, ARCHITECTURE_TERMS):
        return "architecture", "router_architect", ["persistent architecture or semantic decision"], 0.92
    if _contains(text, RESEARCH_TERMS):
        return "research", "router_researcher", ["ambiguous diagnosis, research, or causal inference"], 0.9
    if _contains(text, IMPLEMENTATION_TERMS):
        if task_state == "frozen" or _contains(text, ("existing specification", "according to spec", "已确定", "按照现有", "规格已定")):
            return "implementation", "router_research_engineer", ["implementation after the specification is frozen"], 0.86
        return "research", "router_researcher", ["implementation request still contains unresolved design risk"], 0.66
    if len(text) < 180 and _contains(text, SIMPLE_TERMS):
        return "direct", "direct", ["small bounded task with no specialist signal"], 0.82
    if profile == "quant" and _contains(text, QUANT_TERMS):
        return "research", "router_researcher", ["quant task with unresolved research judgment"], 0.7
    return "direct", "direct", ["no high-risk or specialist signal detected"], 0.58


def _capability_rank(model: str, effort: str) -> tuple[int, int]:
    model_rank = {"gpt-5.6-luna": 1, "gpt-5.6-terra": 2, "gpt-5.6": 3}.get(model, 0)
    effort_rank = {"low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5, "ultra": 6}.get(effort, 0)
    return model_rank, effort_rank


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
    shadow_recommendation: dict[str, Any] | None = None


def _active_override(policy: dict[str, Any], profile: str, task_class: str) -> dict[str, Any] | None:
    for override in reversed(policy.get("overrides", [])):
        if override.get("profile") == profile and override.get("task_class") == task_class:
            return override
    return None


def _shadow_recommendation(profile: str, task_class: str, root: Path | None) -> dict[str, Any] | None:
    shadows = load_shadows(root).get("items", {})
    for proposal_id, item in shadows.items():
        proposal = item.get("proposal", {})
        if item.get("state") == "active" and proposal.get("profile") == profile and proposal.get("task_class") == task_class:
            return {
                "proposal_id": proposal_id,
                "role": proposal.get("to", {}).get("role"),
                "model": proposal.get("to", {}).get("model"),
                "reasoning_effort": proposal.get("to", {}).get("reasoning_effort"),
                "required_successes": item.get("required_successes"),
            }
    return None


def make_route_plan(
    task: str,
    *,
    profile: str | None = None,
    task_state: str = "unknown",
    force_role: str | None = None,
    root: Path | None = None,
) -> RoutePlan:
    if not task.strip():
        raise ValueError("task must not be empty")
    if task_state not in TASK_STATES:
        raise ValueError(f"task_state must be one of: {', '.join(sorted(TASK_STATES))}")
    selected_profile = infer_profile(task, profile)
    loaded_profile = load_profile(selected_profile)
    task_class, role, reasons, confidence = _task_class_and_role(task, selected_profile, task_state, force_role)
    role_config = loaded_profile["roles"].get(role)
    if not isinstance(role_config, dict):
        raise ValueError(f"profile {selected_profile} has no role named {role}")
    model = str(role_config.get("model"))
    effort = str(role_config.get("effort"))
    policy = load_policy(root)
    override = _active_override(policy, selected_profile, task_class)
    if override:
        model = str(override["to"]["model"])
        effort = str(override["to"]["reasoning_effort"])
        role = str(override["to"]["role"])
        reasons.append(f"confirmed policy revision {policy.get('revision')}")
    if model not in VALID_MODELS or effort not in VALID_EFFORTS:
        raise ValueError("profile or policy selected an unsupported model/effort")
    contract = (
        "Return conclusion, compact evidence, uncertainty, and any decision that must return to the primary thread."
        if role != "direct"
        else "Complete directly when safe; delegate only if the work becomes independent and materially useful."
    )
    triggers = [
        "undefined specification or market/system semantics",
        "conflicting evidence or unexplained result",
        "need to change the frozen specification",
        "high-impact architecture, research, or statistical conclusion",
    ]
    return RoutePlan(
        route_id=str(uuid.uuid4()),
        profile=selected_profile,
        task_class=task_class,
        role=role,
        model=model,
        reasoning_effort=effort,
        confidence=round(confidence, 2),
        reasons=reasons,
        escalation_triggers=triggers,
        output_contract=contract,
        shadow_recommendation=_shadow_recommendation(selected_profile, task_class, root),
    )


def create_route_record(
    plan: RoutePlan,
    task: str,
    *,
    session_id: str | None = None,
    project_fingerprint: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    record = {
        "type": "route",
        "schema_version": 1,
        "created_at": utc_now(),
        "route_id": plan.route_id,
        "session": fingerprint(session_id or "manual"),
        "project": fingerprint(project_fingerprint or "unspecified"),
        "task_fingerprint": fingerprint(task),
        "profile": plan.profile,
        "task_class": plan.task_class,
        "role": plan.role,
        "model": plan.model,
        "reasoning_effort": plan.reasoning_effort,
        "confidence": plan.confidence,
        "policy_revision": load_policy(root).get("revision", 1),
        "shadow_proposal_id": (plan.shadow_recommendation or {}).get("proposal_id"),
    }
    _append_jsonl(events_path(root), record)
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
    root: Path | None = None,
) -> dict[str, Any]:
    if status not in OUTCOME_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(OUTCOME_STATUSES))}")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if any(value is not None for value in (replacement_role, replacement_model, replacement_effort)):
        if not all(value is not None for value in (replacement_role, replacement_model, replacement_effort)):
            raise ValueError("replacement role, model, and effort must be supplied together")
        if replacement_model not in VALID_MODELS or replacement_effort not in VALID_EFFORTS:
            raise ValueError("replacement model or effort is unsupported")
    record = {
        "type": "outcome",
        "schema_version": 1,
        "created_at": utc_now(),
        "route_id": route_id,
        "status": status,
        "confidence": confidence,
        "verified": bool(verified),
        "replacement": (
            {"role": replacement_role, "model": replacement_model, "reasoning_effort": replacement_effort}
            if replacement_role is not None
            else None
        ),
    }
    _append_jsonl(events_path(root), record)
    return record


def _events_by_type(root: Path | None = None) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    routes: dict[str, dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    for record in _read_jsonl(events_path(root)):
        if record.get("type") == "route" and record.get("route_id"):
            routes[str(record["route_id"])] = record
        elif record.get("type") == "outcome":
            outcomes.append(record)
    return routes, outcomes


def learning_proposals(root: Path | None = None) -> list[dict[str, Any]]:
    routes, outcomes = _events_by_type(root)
    policy = load_policy(root)
    learning = policy["learning"]
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any], str]]] = {}
    for outcome in outcomes:
        route = routes.get(str(outcome.get("route_id")))
        replacement = outcome.get("replacement")
        if not route or not isinstance(replacement, dict) or outcome.get("status") not in {"corrected", "escalated", "overridden"}:
            continue
        before = _capability_rank(str(route.get("model")), str(route.get("reasoning_effort")))
        after = _capability_rank(str(replacement.get("model")), str(replacement.get("reasoning_effort")))
        if before == after and route.get("role") == replacement.get("role"):
            continue
        direction = "upgrade" if after > before else "downgrade" if after < before else "reroute"
        proposal_key = "|".join(
            [
                str(route.get("profile")),
                str(route.get("task_class")),
                str(route.get("role")),
                str(route.get("model")),
                str(route.get("reasoning_effort")),
                str(replacement.get("role")),
                str(replacement.get("model")),
                str(replacement.get("reasoning_effort")),
            ]
        )
        grouped.setdefault(proposal_key, []).append((route, outcome, direction))
    proposals: list[dict[str, Any]] = []
    for key, observations in grouped.items():
        sessions = {str(route.get("session")) for route, _, _ in observations}
        projects = {str(route.get("project")) for route, _, _ in observations}
        confidence = sum(float(outcome.get("confidence") or 0) for _, outcome, _ in observations) / len(observations)
        first_route, _, direction = observations[-1]
        replacement = observations[-1][1]["replacement"]
        scope = "global" if len(projects) >= int(learning["minimum_distinct_projects_for_global"]) else "repository"
        eligibility = (
            len(sessions) >= int(learning["minimum_independent_sessions"])
            and confidence >= float(learning["minimum_confidence"])
        )
        proposal_id = fingerprint(key)
        shadow = load_shadows(root).get("items", {}).get(proposal_id)
        shadow_state = shadow.get("state") if isinstance(shadow, dict) else "not_started"
        shadow_successes = int(shadow.get("successes") or 0) if isinstance(shadow, dict) else 0
        required = int(learning["shadow_successes_required"])
        status = "collecting_evidence"
        if eligibility:
            status = "ready_for_shadow"
        if shadow_state == "active":
            status = "shadow_running"
        if shadow_state == "validated":
            status = "ready_for_confirmation"
        if shadow_state == "rejected":
            status = "rejected"
        proposals.append(
            {
                "proposal_id": proposal_id,
                "scope": scope,
                "status": status,
                "direction": direction,
                "profile": first_route["profile"],
                "task_class": first_route["task_class"],
                "from": {"role": first_route["role"], "model": first_route["model"], "reasoning_effort": first_route["reasoning_effort"]},
                "to": replacement,
                "independent_sessions": len(sessions),
                "distinct_projects": len(projects),
                "confidence": round(confidence, 3),
                "shadow_successes": shadow_successes,
                "shadow_successes_required": required,
            }
        )
    return sorted(proposals, key=lambda item: (item["status"], -item["independent_sessions"], item["proposal_id"]))


def _proposal_by_id(proposal_id: str, root: Path | None = None) -> dict[str, Any]:
    proposal = next((item for item in learning_proposals(root) if item["proposal_id"] == proposal_id), None)
    if proposal is None:
        raise ValueError("proposal_id has no eligible routing evidence")
    return proposal


def start_shadow(proposal_id: str, root: Path | None = None) -> dict[str, Any]:
    proposal = _proposal_by_id(proposal_id, root)
    if proposal["status"] != "ready_for_shadow":
        raise ValueError("proposal must be ready_for_shadow before shadow evaluation")
    shadows = load_shadows(root)
    shadows["items"][proposal_id] = {
        "state": "active",
        "started_at": utc_now(),
        "required_successes": proposal["shadow_successes_required"],
        "successes": 0,
        "failures": 0,
        "proposal": proposal,
    }
    save_shadows(shadows, root)
    return shadows["items"][proposal_id]


def record_shadow_observation(proposal_id: str, success: bool, root: Path | None = None) -> dict[str, Any]:
    shadows = load_shadows(root)
    item = shadows["items"].get(proposal_id)
    if not isinstance(item, dict) or item.get("state") != "active":
        raise ValueError("proposal is not in active shadow evaluation")
    if success:
        item["successes"] = int(item.get("successes") or 0) + 1
    else:
        item["failures"] = int(item.get("failures") or 0) + 1
    if int(item["successes"]) >= int(item["required_successes"]):
        item["state"] = "validated"
        item["validated_at"] = utc_now()
    elif int(item["failures"]) >= 2:
        item["state"] = "rejected"
        item["rejected_at"] = utc_now()
    save_shadows(shadows, root)
    return item


def confirm_policy_change(proposal_id: str, confirmed_by_user: bool, root: Path | None = None) -> dict[str, Any]:
    if not confirmed_by_user:
        raise ValueError("policy changes require explicit user confirmation")
    proposal = _proposal_by_id(proposal_id, root)
    shadows = load_shadows(root)
    shadow = shadows["items"].get(proposal_id)
    if proposal["status"] != "ready_for_confirmation" or not isinstance(shadow, dict):
        raise ValueError("proposal requires successful shadow validation before confirmation")
    policy = load_policy(root)
    override = {
        "proposal_id": proposal_id,
        "profile": proposal["profile"],
        "task_class": proposal["task_class"],
        "to": proposal["to"],
        "scope": proposal["scope"],
        "confirmed_at": utc_now(),
    }
    policy["overrides"] = [
        item
        for item in policy.get("overrides", [])
        if not (item.get("profile") == override["profile"] and item.get("task_class") == override["task_class"])
    ]
    policy["overrides"].append(override)
    policy["revision"] = int(policy.get("revision") or 1) + 1
    save_policy(policy, root)
    shadow["state"] = "confirmed"
    shadow["confirmed_at"] = utc_now()
    save_shadows(shadows, root)
    return override


def gardener_candidates(root: Path | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for proposal in learning_proposals(root):
        if proposal["status"] not in {"ready_for_shadow", "ready_for_confirmation"}:
            continue
        candidates.append(
            {
                "proposal_id": proposal["proposal_id"],
                "knowledge_scope": proposal["scope"],
                "scope": f"adaptive routing: {proposal['profile']} {proposal['task_class']}",
                "lesson": (
                    f"For {proposal['profile']} {proposal['task_class']} tasks, prefer "
                    f"{proposal['to']['role']} ({proposal['to']['model']} {proposal['to']['reasoning_effort']}) "
                    f"after repeated validated under/over-routing evidence."
                ),
                "evidence": (
                    f"{proposal['independent_sessions']} independent sessions across {proposal['distinct_projects']} project fingerprints; "
                    f"mean outcome confidence {proposal['confidence']}."
                ),
                "recommended_target": "skill",
                "confidence": proposal["confidence"],
                "status": proposal["status"],
            }
        )
    return candidates


def policy_status(root: Path | None = None) -> dict[str, Any]:
    policy = load_policy(root)
    routes, outcomes = _events_by_type(root)
    return {
        "policy_revision": policy.get("revision"),
        "profiles": available_profiles(),
        "stored_routes": len(routes),
        "stored_outcomes": len(outcomes),
        "active_overrides": len(policy.get("overrides", [])),
        "proposals": learning_proposals(root),
        "gardener_candidates": gardener_candidates(root),
    }


def hook_context(event: str, payload: dict[str, Any], root: Path | None = None) -> dict[str, Any] | None:
    if event == "SessionStart":
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    "Codex Adaptive Router is active. Keep the primary thread on final intent and integration; "
                    "use explicit model/effort subagent routes only when work is independent and materially useful."
                ),
            }
        }
    if event == "UserPromptSubmit":
        task = str(payload.get("prompt") or "").strip()
        if not task:
            return None
        plan = make_route_plan(task, root=root)
        shadow = ""
        if plan.shadow_recommendation:
            shadow = f" A shadow recommendation exists for this task class: {plan.shadow_recommendation['role']}; do not apply it yet."
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    f"Adaptive Router preflight (hint, not an override): profile={plan.profile}; "
                    f"route={plan.role}; model={plan.model}; effort={plan.reasoning_effort}. "
                    "For non-trivial work, call adaptive_router.route_plan before delegation. "
                    "User model/effort instructions win. Luna/Terra must escalate unresolved research or architecture decisions to Sol."
                    + shadow
                ),
            }
        }
    if event == "SubagentStart":
        return {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": (
                    "Follow the assigned routing role. Return compact evidence, uncertainty, and escalation triggers; "
                    "do not silently make an unresolved high-impact decision outside that role."
                ),
            }
        }
    return None


def record_hook_event(event: str, payload: dict[str, Any], root: Path | None = None) -> None:
    session_id = str(payload.get("session_id") or "unknown")
    turn_id = str(payload.get("turn_id") or "")
    record: dict[str, Any] = {
        "type": "hook",
        "schema_version": 1,
        "created_at": utc_now(),
        "event": event,
        "session": fingerprint(session_id),
        "turn": fingerprint(turn_id) if turn_id else None,
    }
    if event == "PostToolUse":
        response = payload.get("tool_response")
        exit_code = 0
        if isinstance(response, dict):
            try:
                exit_code = int(response.get("exit_code", response.get("exitCode", 0)) or 0)
            except (TypeError, ValueError):
                exit_code = 1
        failed = isinstance(response, dict) and (
            response.get("isError") is True or response.get("is_error") is True or exit_code != 0
        )
        record.update({"tool": str(payload.get("tool_name") or "unknown"), "failed": failed})
    elif event in {"SubagentStart", "SubagentStop"}:
        record["agent_type"] = str(payload.get("agent_type") or "unknown")[:120]
    _append_jsonl(events_path(root), record)
