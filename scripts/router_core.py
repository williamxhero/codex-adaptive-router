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
QUALITY_GATES = {"unknown", "provisional", "passed", "failed"}
ROUTE_FITS = {"unknown", "adequate", "under_routed", "over_routed"}
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
    "router_architect",
    "router_adversarial_auditor",
    "router_strategy_scout",
}
VERIFICATION_KINDS = {"tests", "build", "static_validation", "review"}
HOOK_EVENTS = {
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SessionEnd",
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


def validate_decision_features(value: Any) -> dict[str, Any]:
    required = {
        "operation_mode", "scope", "spec_state", "reversibility", "cognitive_type",
        "risk_domains", "workload", "user_constraints", "feature_source", "confidence",
    }
    features = _strict_object(value, required, required, "decision_features")
    for key, allowed in FEATURE_VALUES.items():
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
        value, {"phase", "role", "model", "reasoning_effort"},
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


def _validate_replacement(value: Any) -> None:
    if value is None:
        return
    replacement = _strict_object(
        value, {"role", "model", "reasoning_effort"}, {"role", "model", "reasoning_effort"}, "replacement"
    )
    if replacement["role"] not in VALID_ROLES or replacement["model"] not in VALID_MODELS or replacement["reasoning_effort"] not in VALID_EFFORTS:
        raise ValueError("replacement route is invalid")


def validate_evidence_event(value: Any) -> dict[str, Any]:
    common = {"schema_version", "event_id", "sequence", "created_at", "dedupe_key", "type"}
    route = {
        "task_ref", "task_fingerprint", "route_id", "profile", "task_class", "role", "model",
        "reasoning_effort", "confidence", "decision_features", "constraints", "policy_revision",
        "shadow", "session", "project",
    }
    execution = {"task_ref", "route_id", "event", "tool_kind", "failed", "verification_kind", "transition"}
    outcome = {
        "task_ref", "route_id", "status", "quality_gate", "route_fit", "verification_kinds",
        "confidence", "evidence_source", "objective_verification", "user_confirmed", "replacement",
        "high_risk_regression", "retry_band", "rework_band", "tool_band", "duration_band",
        "token_band", "cost_band",
    }
    if not isinstance(value, dict) or value.get("type") not in {"route", "execution", "outcome"}:
        raise ValueError("evidence event type is invalid")
    event_type = value["type"]
    allowed = common | ({"route": route, "execution": execution, "outcome": outcome}[event_type])
    required = common - {"dedupe_key"} | ({"route": route, "execution": {"task_ref", "route_id", "event"}, "outcome": outcome}[event_type])
    event = _strict_object(value, allowed, required, f"{event_type} event")
    if event["schema_version"] != 2 or not isinstance(event["sequence"], int) or isinstance(event["sequence"], bool) or event["sequence"] < 1:
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
        if not _is_number(event["confidence"]) or not 0 <= event["confidence"] <= 1:
            raise ValueError("route confidence is invalid")
        if not isinstance(event["policy_revision"], int) or isinstance(event["policy_revision"], bool) or event["policy_revision"] < 1:
            raise ValueError("policy_revision is invalid")
        validate_decision_features(event["decision_features"])
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
    return event


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def plugin_data_root() -> Path:
    explicit = os.environ.get("CODEX_ADAPTIVE_ROUTER_DATA") or os.environ.get(
        "PLUGIN_DATA"
    )
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (
        Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        / "codex-adaptive-router"
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
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(32)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
    except FileExistsError:
        raw = path.read_bytes()
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
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


def infer_profile(task: str, requested: str | None = None) -> str:
    if requested in available_profiles():
        return str(requested)
    return "quant" if _contains(_normalise(task), QUANT_TERMS) else "generic"


def infer_decision_features(
    task: str, *, task_state: str = "unknown", supplied: dict[str, Any] | None = None
) -> dict[str, Any]:
    text = _normalise(task)
    features = {
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
    if supplied:
        allowed = set(features)
        for key, value in supplied.items():
            if key not in allowed:
                raise ValueError(f"unknown decision feature: {key}")
            if key in FEATURE_VALUES and value not in FEATURE_VALUES[key]:
                raise ValueError(f"invalid decision feature {key}: {value}")
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
    shadow_recommendation: dict[str, Any] | None = None


def _capability_rank(model: str, effort: str) -> tuple[int, int]:
    return (
        {"gpt-5.6-luna": 1, "gpt-5.6-terra": 2, "gpt-5.6-sol": 3}.get(model, 0),
        {"low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5, "ultra": 6}.get(
            effort, 0
        ),
    )


def _active_overrides(
    policy: dict[str, Any], profile: str, task_class: str
) -> dict[str, str]:
    result = {}
    for item in policy.get("overrides", []):
        if item.get("profile") != profile or item.get("task_class") != task_class:
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
    profile: str, task_class: str, root: Path | None
) -> dict[str, Any] | None:
    for proposal_id, item in load_shadows(root)["items"].items():
        proposal = item.get("proposal", {})
        if (
            item.get("state") == "active"
            and proposal.get("profile") == profile
            and proposal.get("task_class") == task_class
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


def make_route_plan(
    task: str,
    *,
    profile: str | None = None,
    task_state: str = "unknown",
    force_role: str | None = None,
    decision_features: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    route_id: str | None = None,
    root: Path | None = None,
) -> RoutePlan:
    if not task.strip():
        raise ValueError("task must not be empty")
    if task_state not in TASK_STATES:
        raise ValueError("invalid task_state")
    selected = infer_profile(task, profile)
    features = infer_decision_features(
        task, task_state=task_state, supplied=decision_features
    )
    task_class = str(features["cognitive_type"])
    if task_class == "implementation" and features["spec_state"] != "frozen":
        task_class = "research"
    role = force_role or ROLE_BY_COGNITIVE.get(task_class, "direct")
    constraints = validate_constraints(constraints)
    loaded = load_profile(selected)
    override = _active_overrides(load_policy(root), selected, task_class)
    if not force_role and "role" not in constraints and not constraints.get("no_delegation"):
        role = override.get("role", role)
    if constraints.get("no_delegation"):
        role = "direct"
    elif constraints.get("role"):
        role = str(constraints["role"])
    config = _role_config(loaded, role)
    model = str(config.get("default_model") or config.get("model"))
    effort_config = config.get("effort")
    effort = str(
        config.get("effort_default")
        or (
            effort_config
            if isinstance(effort_config, str)
            else effort_config.get("default")
        )
    )
    if "model" not in constraints:
        model = override.get("model", model)
    if "reasoning_effort" not in constraints:
        effort = override.get("reasoning_effort", effort)
    model = str(constraints.get("model", model))
    effort = str(constraints.get("reasoning_effort", effort))
    config = _validate_route_tuple(
        loaded,
        role,
        model,
        effort,
        explicit_effort="reasoning_effort" in constraints,
    )
    if role in {
        "router_research_engineer",
        "router_code_mapper",
        "router_experiment_runner",
    } and task_class in {"architecture", "diagnosis", "research", "audit"}:
        raise ValueError(
            "Luna/Terra roles cannot own unresolved semantics or research conclusions"
        )
    escalation = list(
        config.get("sol_escalation_conditions")
        or [
            "undefined semantics",
            "unresolved research conclusion",
            "irreversible decision",
        ]
    )
    return RoutePlan(
        route_id or str(uuid.uuid4()),
        selected,
        task_class,
        role,
        model,
        effort,
        round(float(features["confidence"]), 2),
        [
            "structured decision features",
            f"cognitive_type={features['cognitive_type']}",
        ],
        escalation,
        "Return conclusion, compact evidence, uncertainty, and decisions reserved for the primary thread.",
        features,
        constraints,
        _shadow_recommendation(selected, task_class, root),
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
                    )
                }
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
                    },
                }
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
        value.update(
            schema_version=2,
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
            },
            f"followup:{previous['task_ref']}:{label}",
        )

    def plan_route(
        self,
        task: str | None = None,
        *,
        task_ref: str | None = None,
        session_id: str | None = None,
        project_fingerprint: str | None = None,
        profile: str | None = None,
        task_state: str = "unknown",
        force_role: str | None = None,
        decision_features: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        record: bool = True,
    ) -> dict[str, Any]:
        with _file_lock(ledger_path(self.root)):
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
                    "project": identity(
                        project_fingerprint or "unspecified", self.root
                    ),
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
                if transition:
                    agg["transitions"].append(transition)
                    sanitized["transition"] = transition
            elif event in {"SubagentStart", "SubagentStop"}:
                transition = {
                    "phase": "start" if event.endswith("Start") else "stop",
                    "role": self._safe_role(payload.get("agent_type")),
                    "model": self._safe_model(payload.get("model")),
                    "reasoning_effort": "unknown",
                }
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
            return {"gpt-5.6-luna": 1, "gpt-5.6-terra": 2, "gpt-5.6-sol": 3}.get(
                str(value), 0
            )
        if axis == "reasoning_effort":
            return {
                "low": 1,
                "medium": 2,
                "high": 3,
                "xhigh": 4,
                "max": 5,
                "ultra": 6,
            }.get(str(value), 0)
        return 1

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
            agg = task["aggregate"]
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
            }
            record = self._append(
                ledger,
                outcome,
                f"outcome:{task_ref}:{status}:{quality_gate}:{route_fit}",
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
        actual = task["route"].get(axis)
        if (
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
            or outcome.get("quality_gate") not in {"passed", "failed"}
            or float(outcome.get("confidence") or 0) < learning["minimum_confidence"]
        ):
            continue
        for axis in ("role", "model", "reasoning_effort"):
            before, after = str(route.get(axis)), str(replacement.get(axis))
            if before == after:
                continue
            profile_config = load_profile(str(route.get("profile")))
            try:
                if axis == "role":
                    _role_config(profile_config, after)
                elif axis == "model":
                    _validate_route_tuple(
                        profile_config,
                        str(route.get("role")),
                        after,
                        str(route.get("reasoning_effort")),
                    )
                else:
                    _validate_route_tuple(
                        profile_config,
                        str(route.get("role")),
                        str(route.get("model")),
                        after,
                    )
            except ValueError:
                continue
            key = "|".join(
                [
                    str(route.get("profile")),
                    str(route.get("task_class")),
                    axis,
                    before,
                    after,
                ]
            )
            grouped.setdefault(key, []).append((route, outcome, axis, before, after))
    result = []
    for key, items in grouped.items():
        sessions = {str(x[0].get("session")) for x in items}
        projects = {str(x[0].get("project")) for x in items}
        confidence = sum(float(x[1]["confidence"]) for x in items) / len(items)
        route, _, axis, before, after = items[-1]
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
    unnecessary = sum(
        x.get("route_fit") == "over_routed"
        and (
            routes.get(str(x.get("route_id")), {}).get("model") == "gpt-5.6-sol"
            or routes.get(str(x.get("route_id")), {}).get("reasoning_effort") == "xhigh"
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
    return {
        "schema_version": 2,
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
                "additionalContext": "Codex Adaptive Router v1.1 is active; policy changes remain human-confirmed.",
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
                "additionalContext": f"Adaptive Router task_ref={task['task_ref']}; initial route={route['role']}; model={route['model']}; effort={route['reasoning_effort']}. Call route_plan with this task_ref to confirm or refine without rerouting. User constraints win; unresolved semantics escalate to Sol.",
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
