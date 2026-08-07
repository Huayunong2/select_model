"""Task profiling with explicit uncertainty and conservative policy selection."""
from __future__ import annotations

import re
from typing import Any

from .constants import BUCKETS, FEATURES, FEATURE_DEFAULTS, POLICY_MODES, RISK, TASK_FAMILY_ALIASES
from .errors import ValidationError
from .utils import clamp, num


def task_family(task_type: Any) -> str:
    normalized = str(task_type or "").strip().lower().replace("_", ".").replace("-", ".")
    if not normalized:
        return "general"
    root = normalized.split(".", 1)[0]
    if root in TASK_FAMILY_ALIASES:
        return TASK_FAMILY_ALIASES[root]
    return root if root in {"coding", "research", "writing", "analysis", "agent", "document", "vision", "general"} else "general"


def _parse_feature(key: str, raw: Any) -> tuple[float, float, str]:
    default_value, default_confidence = FEATURE_DEFAULTS[key]
    if raw is None:
        return default_value, default_confidence, "default"
    if isinstance(raw, dict):
        confidence = num(raw.get("confidence"))
        confidence = clamp(confidence if confidence is not None else 0.75)
        explicit = num(raw.get("value"))
        if explicit is not None:
            return clamp(explicit), confidence, "explicit_value"
        bucket = str(raw.get("bucket", "")).strip().lower()
        if bucket in BUCKETS:
            return BUCKETS[bucket], confidence, f"bucket:{bucket}"
        return default_value, min(confidence, 0.30), "invalid_default"
    explicit = num(raw)
    if explicit is not None:
        return clamp(explicit), 0.65, "explicit_value"
    bucket = str(raw).strip().lower()
    if bucket in BUCKETS:
        return BUCKETS[bucket], 0.60, f"bucket:{bucket}"
    return default_value, default_confidence, "default"


def feature_vector(task: dict[str, Any]) -> dict[str, Any]:
    raw = task.get("features") if isinstance(task.get("features"), dict) else {}
    values: dict[str, float] = {}
    confidence: dict[str, float] = {}
    sources: dict[str, str] = {}
    missing: list[str] = []
    for key in FEATURES:
        value, item_confidence, source = _parse_feature(key, raw.get(key))
        values[key] = value
        confidence[key] = item_confidence
        sources[key] = source
        if source in {"default", "invalid_default"}:
            missing.append(key)
    weights = {
        "reasoning": 1.2,
        "context": 1.0,
        "unfamiliarity": 0.7,
        "tools": 0.8,
        "browser": 0.4,
        "cross_file": 1.0,
        "test_quality": 0.8,
        "detectability": 1.0,
        "rollback": 1.0,
        "ambiguity": 0.9,
        "horizon": 1.2,
    }
    total = sum(weights.values())
    overall = sum(weights[key] * confidence[key] for key in FEATURES) / total
    return {
        "values": values,
        "confidence": confidence,
        "source": sources,
        "overall_confidence": round(overall, 3),
        "missing": missing,
    }


def resolve_policy_mode(task: dict[str, Any], risk_name: str) -> str:
    requested = str(task.get("policy_mode", "auto")).strip().lower()
    if requested not in POLICY_MODES:
        raise ValidationError("task.policy_mode must be auto, explore, balanced, or strict")
    if requested != "auto":
        if risk_name in {"high", "critical"} and requested != "strict":
            # Never allow high-risk callers to silently weaken eligibility rules.
            return "strict"
        return requested
    if risk_name in {"high", "critical"}:
        return "strict"
    if risk_name == "low":
        return "explore"
    return "balanced"


def task_pressures(task: dict[str, Any]) -> dict[str, Any]:
    vector = feature_vector(task)
    features = vector["values"]
    risk_name = str(task.get("risk", "medium")).lower()
    if risk_name not in RISK:
        raise ValidationError("task.risk must be low, medium, high, or critical")
    risk = RISK[risk_name]
    reasoning = (
        0.32 * features["reasoning"]
        + 0.17 * features["cross_file"]
        + 0.16 * features["ambiguity"]
        + 0.18 * features["horizon"]
        + 0.17 * features["unfamiliarity"]
    )
    context = (
        0.50 * features["context"]
        + 0.20 * features["cross_file"]
        + 0.20 * features["unfamiliarity"]
        + 0.10 * features["horizon"]
    )
    tools = (
        0.50 * features["tools"]
        + 0.15 * features["browser"]
        + 0.20 * features["horizon"]
        + 0.15 * features["cross_file"]
    )
    reliability = (
        0.20 * (1.0 - features["test_quality"])
        + 0.25 * (1.0 - features["detectability"])
        + 0.25 * (1.0 - features["rollback"])
        + 0.30 * risk
    )
    average = 0.35 * reasoning + 0.25 * context + 0.15 * tools + 0.25 * reliability
    overall = clamp(0.70 * average + 0.30 * max(reasoning, context, tools, reliability))
    if overall < 0.30:
        difficulty = "easy"
    elif overall < 0.47:
        difficulty = "normal"
    elif overall < 0.62:
        difficulty = "hard"
    elif overall < 0.78:
        difficulty = "very_hard"
    else:
        difficulty = "extreme"
    plan = (
        features["horizon"] >= 0.55
        or features["ambiguity"] >= 0.50
        or features["cross_file"] >= 0.70
        or features["tools"] >= 0.75
    )
    return {
        "feature_vector": vector,
        "risk": risk_name,
        "policy_mode": resolve_policy_mode(task, risk_name),
        "task_type": task.get("task_type", "unknown"),
        "task_family": task_family(task.get("task_type")),
        "reasoning": round(reasoning, 3),
        "context": round(context, 3),
        "tools": round(tools, 3),
        "reliability": round(reliability, 3),
        "overall": round(overall, 3),
        "display_difficulty": difficulty,
        "plan_recommended": plan,
    }


def _contains(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def infer_task_profile(seed: dict[str, Any]) -> dict[str, Any]:
    """Infer a conservative profile from a description; all inferred fields retain limited confidence."""
    description = str(seed.get("description") or seed.get("task") or seed.get("text") or "").strip()
    if not description:
        raise ValidationError("profile input requires description, task, or text")
    task_type = str(seed.get("task_type") or "general")
    lowered = description.lower()
    features: dict[str, dict[str, Any]] = {}

    def set_feature(name: str, bucket: str, confidence: float) -> None:
        features[name] = {"bucket": bucket, "confidence": confidence}

    if _contains(lowered, [r"\bdebug\b", r"race condition", r"architecture", r"证明", r"复杂推理", r"根因"]):
        set_feature("reasoning", "high", 0.58)
    if _contains(lowered, [r"repository", r"repo", r"monorepo", r"全仓", r"跨文件", r"codebase"]):
        set_feature("cross_file", "high", 0.62)
        set_feature("context", "high", 0.58)
    if _contains(lowered, [r"browser", r"web", r"search", r"网页", r"检索", r"research"]):
        set_feature("browser", "high", 0.65)
        set_feature("tools", "high", 0.55)
    if _contains(lowered, [r"agent", r"workflow", r"automation", r"自动化", r"工具调用"]):
        set_feature("tools", "high", 0.62)
        set_feature("horizon", "high", 0.55)
    if _contains(lowered, [r"production", r"payment", r"security", r"医疗", r"法律", r"生产", r"高风险"]):
        seed.setdefault("risk", "high")
        set_feature("rollback", "low", 0.55)
        set_feature("detectability", "low", 0.50)
    if _contains(lowered, [r"tests?", r"pytest", r"unit test", r"测试完善", r"有测试"]):
        set_feature("test_quality", "high", 0.60)
        set_feature("detectability", "high", 0.55)
    if _contains(lowered, [r"unclear", r"ambiguous", r"explore", r"开放问题", r"不明确", r"探索"]):
        set_feature("ambiguity", "high", 0.60)
        set_feature("unfamiliarity", "high", 0.52)

    task = {
        "task_type": task_type,
        "risk": seed.get("risk", "medium"),
        "target_runtime": seed.get("target_runtime", "advisor"),
        "policy_mode": seed.get("policy_mode", "auto"),
        "features": features,
    }
    for field in (
        "repo_id",
        "environment_id",
        "estimated_context_tokens",
        "estimated_output_tokens",
        "required_capabilities",
        "usage_estimate",
        "failure_cost",
        "time_value_per_hour",
    ):
        if field in seed:
            task[field] = seed[field]
    return {"task": task, "profile": task_pressures(task), "inference_notice": "Heuristic inference; review low-confidence dimensions before high-risk routing."}
