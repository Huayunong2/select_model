"""Privacy-aware, time-decayed personal execution history."""
from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback remains append-only.
    fcntl = None  # type: ignore[assignment]

from .constants import HISTORY_SCHEMA_VERSION
from .errors import ValidationError
from .task import feature_vector, task_family
from .utils import boolish, clamp, num, parse_datetime, sanitize_identifier, sha256_json, utc_now, utc_now_iso


def load_history(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    if not source.exists():
        return {"rows": [], "invalid_lines": 0, "path": str(source), "warnings": []}
    rows: list[dict[str, Any]] = []
    invalid = 0
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            invalid += 1
    warnings = [f"{invalid} invalid JSONL line(s) were ignored"] if invalid else []
    return {"rows": rows, "invalid_lines": invalid, "path": str(source), "warnings": warnings}


def _task_type_factor(current: dict[str, Any], past: dict[str, Any]) -> tuple[float, str]:
    current_type = str(current.get("task_type", "")).strip().lower()
    past_type = str(past.get("task_type", "")).strip().lower()
    current_family = task_family(current_type)
    past_family = task_family(past_type)
    if current_type and current_type == past_type:
        return 1.20, "exact_task_type"
    if current_family == past_family:
        return 0.55, "same_task_family"
    return 0.05, "different_task_family"


def _task_similarity(current: dict[str, Any], past: dict[str, Any]) -> tuple[float, str]:
    current_features = feature_vector(current)["values"]
    past_features = feature_vector(past)["values"]
    weights = {
        "reasoning": 1.2,
        "context": 1.0,
        "unfamiliarity": 0.7,
        "tools": 0.8,
        "browser": 0.35,
        "cross_file": 1.0,
        "test_quality": 0.7,
        "detectability": 0.8,
        "rollback": 0.8,
        "ambiguity": 0.8,
        "horizon": 1.1,
    }
    total = sum(weights.values())
    squared = sum(weights[key] * (current_features[key] - past_features[key]) ** 2 for key in weights) / total
    similarity = math.exp(-4.0 * math.sqrt(squared))
    type_factor, reason = _task_type_factor(current, past)
    similarity *= type_factor
    current_repo = str(current.get("repo_id", "")).strip()
    past_repo = str(past.get("repo_id", "")).strip()
    if current_repo and past_repo:
        similarity *= 1.12 if current_repo == past_repo else 0.45
    current_environment = str(current.get("environment_id", "")).strip()
    past_environment = str(past.get("environment_id", "")).strip()
    if current_environment and past_environment:
        similarity *= 1.06 if current_environment == past_environment else 0.75
    return min(1.0, similarity), reason


def _time_weight(row: dict[str, Any], *, now: datetime, half_life_days: float) -> float:
    recorded = parse_datetime(row.get("recorded_at") or row.get("meta", {}).get("recorded_at"))
    if recorded is None:
        return 0.55
    age_days = max(0.0, (now - recorded).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age_days / max(half_life_days, 1.0))


def _snapshot_weight(candidate: dict[str, Any], execution: dict[str, Any]) -> float:
    current = str(candidate.get("snapshot") or candidate.get("model_snapshot") or candidate.get("model") or "").strip()
    past = str(execution.get("model_snapshot", "")).strip()
    if current and past:
        return 1.0 if current == past else 0.45
    return 0.72


def _execution_success(execution: dict[str, Any]) -> bool | None:
    direct = boolish(execution.get("first_pass_success"))
    if direct is not None:
        return direct
    direct = boolish(execution.get("success"))
    if direct is not None:
        return direct
    final = boolish(execution.get("final_success"))
    retry = boolish(execution.get("user_retry")) or False
    fallback = boolish(execution.get("fallback_triggered")) or False
    tests = boolish(execution.get("tests_passed"))
    if final is not None:
        return bool(final and not retry and not fallback)
    if tests is not None:
        return bool(tests and not retry and not fallback)
    return None


def _product_mode(value: dict[str, Any]) -> str:
    """Return the product mode while accepting the v3 ``mode`` alias."""
    return str(value.get("product_mode", value.get("mode", "standard"))).lower()


def _same_configuration(candidate: dict[str, Any], execution: dict[str, Any]) -> bool:
    return (
        str(execution.get("model", "")) == str(candidate.get("model", ""))
        and str(execution.get("effort", "")).lower() == str(candidate.get("effort", "")).lower()
        and _product_mode(execution) == _product_mode(candidate)
    )


def personal_calibration(
    task: dict[str, Any],
    candidate: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    as_of: datetime | None = None,
    half_life_days: float = 120.0,
) -> dict[str, Any]:
    reference = (as_of or now or utc_now()).astimezone(UTC)
    exact_success = 0.0
    exact_effective = 0.0
    exact_raw = 0
    same_family = 0
    exact_task_type = 0
    broader_success = 0.0
    broader_effective = 0.0

    for row in history:
        execution = row.get("execution") if isinstance(row.get("execution"), dict) else {}
        past_task = row.get("task") if isinstance(row.get("task"), dict) else {}
        success = _execution_success(execution)
        if success is None:
            continue
        similarity, relation = _task_similarity(task, past_task)
        if relation == "different_task_family" or similarity < 0.02:
            continue
        recency = _time_weight(row, now=reference, half_life_days=half_life_days)
        snapshot = _snapshot_weight(candidate, execution)
        if _same_configuration(candidate, execution):
            weight = similarity * recency * snapshot
            if weight < 0.02:
                continue
            exact_raw += 1
            if relation in {"exact_task_type", "same_task_family"}:
                same_family += 1
            if relation == "exact_task_type":
                exact_task_type += 1
            exact_effective += weight
            exact_success += weight * (1.0 if success else 0.0)
        elif str(execution.get("model", "")) == str(candidate.get("model", "")):
            # Same model, different effort/mode contributes only a strongly capped prior.
            weight = similarity * recency * snapshot * 0.18
            broader_effective += weight
            broader_success += weight * (1.0 if success else 0.0)

    prior_strength = min(4.0, broader_effective)
    if broader_effective > 0:
        broader_rate = broader_success / broader_effective
        alpha = 2.0 + prior_strength * broader_rate
        beta = 2.0 + prior_strength * (1.0 - broader_rate)
    else:
        alpha = beta = 2.0
    probability = (exact_success + alpha) / (exact_effective + alpha + beta)
    variance = probability * (1.0 - probability) / max(exact_effective + alpha + beta + 1.0, 1.0)
    standard_error = math.sqrt(max(variance, 0.0))
    interval = [clamp(probability - 1.64 * standard_error), clamp(probability + 1.64 * standard_error)]

    available = exact_effective >= 8.0 and same_family >= 10 and exact_task_type >= 4
    confidence = "unavailable"
    if available:
        confidence = "high" if exact_effective >= 50 and same_family >= 35 else "medium" if exact_effective >= 24 else "low"
    return {
        "available": available,
        "status": "calibrated" if available else "provisional",
        "p_success": round(probability, 4) if available else None,
        "provisional_p_success": round(probability, 4),
        "interval_90": [round(item, 4) for item in interval],
        "confidence": confidence,
        "raw_n": exact_raw,
        "same_family_n": same_family,
        "exact_type_n": exact_task_type,
        "effective_n": round(exact_effective, 2),
        "prior_effective_n": round(prior_strength, 2),
        "half_life_days": half_life_days,
        "reason": None if available else "need >=8 effective same-configuration attempts, >=10 same-family records, and >=4 exact task-type records",
    }


def history_usage_estimate(
    task: dict[str, Any],
    candidate: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    as_of: datetime | None = None,
    half_life_days: float = 120.0,
) -> dict[str, Any] | None:
    reference = (as_of or now or utc_now()).astimezone(UTC)
    fields = (
        "input_tokens",
        "uncached_input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "tool_cost",
        "latency_seconds",
    )
    sums = {field: 0.0 for field in fields}
    weights = {field: 0.0 for field in fields}
    effective = 0.0
    for row in history:
        execution = row.get("execution") if isinstance(row.get("execution"), dict) else {}
        past_task = row.get("task") if isinstance(row.get("task"), dict) else {}
        if not _same_configuration(candidate, execution):
            continue
        similarity, relation = _task_similarity(task, past_task)
        if relation == "different_task_family" or similarity < 0.05:
            continue
        weight = similarity * _time_weight(row, now=reference, half_life_days=half_life_days) * _snapshot_weight(candidate, execution)
        if weight < 0.03:
            continue
        effective += weight
        usage = execution.get("usage") if isinstance(execution.get("usage"), dict) else {}
        for field in fields:
            value = num(execution.get(field)) if field == "latency_seconds" else num(usage.get(field))
            if value is not None:
                sums[field] += weight * value
                weights[field] += weight
    if effective < 3.0:
        return None
    output = {field: sums[field] / weights[field] if weights[field] else None for field in fields}
    output["effective_n"] = round(effective, 2)
    return output


def _normalized_task(task: dict[str, Any], *, hash_identifiers: bool, hash_salt: str) -> dict[str, Any]:
    output = {
        "task_type": task.get("task_type", "unknown"),
        "risk": task.get("risk", "medium"),
        "repo_id": task.get("repo_id"),
        "environment_id": task.get("environment_id"),
        "features": feature_vector(task)["values"],
    }
    if hash_identifiers:
        output["repo_id"] = sanitize_identifier(output["repo_id"], salt=hash_salt)
        output["environment_id"] = sanitize_identifier(output["environment_id"], salt=hash_salt)
    return output


def build_attempt_from_artifacts(
    route: dict[str, Any], response: dict[str, Any], outcome: dict[str, Any]
) -> dict[str, Any]:
    selected = route.get("selected") if isinstance(route.get("selected"), dict) else {}
    task = route.get("task_input") if isinstance(route.get("task_input"), dict) else {}
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    input_details = (
        usage.get("input_tokens_details")
        if isinstance(usage.get("input_tokens_details"), dict)
        else {}
    )
    cached_tokens = usage.get("cached_input_tokens")
    if cached_tokens is None:
        cached_tokens = input_details.get("cached_tokens")
    normalized_usage = {
        "input_tokens": usage.get("input_tokens"),
        "uncached_input_tokens": usage.get("uncached_input_tokens"),
        "cached_input_tokens": cached_tokens,
        "cache_write_tokens": usage.get("cache_write_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "tool_cost": outcome.get("tool_cost", usage.get("tool_cost")),
    }
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "route_id": route.get("route_id"),
        "task": task,
        "execution": {
            "model": selected.get("model"),
            "effort": selected.get("effort"),
            "product_mode": _product_mode(selected),
            "model_snapshot": selected.get("snapshot") or selected.get("model_snapshot") or route.get("registry_version"),
            "success": outcome.get("success"),
            "first_pass_success": outcome.get("first_pass_success"),
            "tests_passed": outcome.get("tests_passed"),
            "user_retry": outcome.get("user_retry"),
            "fallback_triggered": outcome.get("fallback_triggered"),
            "final_success": outcome.get("final_success"),
            "quality_score": outcome.get("quality_score"),
            "human_edit_minutes": outcome.get("human_edit_minutes"),
            "latency_seconds": outcome.get("latency_seconds") or response.get("latency_seconds"),
            "cost": outcome.get("cost"),
            "usage": normalized_usage,
        },
        "meta": {"response_id": response.get("id")},
    }


def _normalize_attempt(data: dict[str, Any], *, hash_identifiers: bool, hash_salt: str) -> dict[str, Any]:
    task = data.get("task")
    execution = data.get("execution")
    if not isinstance(task, dict) or not isinstance(execution, dict):
        raise ValidationError("attempt requires task and execution objects")
    model = str(execution.get("model", "")).strip()
    effort = str(execution.get("effort", "")).strip().lower()
    if not model or not effort:
        raise ValidationError("execution.model and execution.effort are required")
    success = _execution_success(execution)
    if success is None:
        raise ValidationError("attempt requires a derivable first-pass success outcome")
    usage_in = execution.get("usage") if isinstance(execution.get("usage"), dict) else {}
    usage = {field: num(usage_in.get(field)) for field in (
        "input_tokens", "uncached_input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "tool_cost"
    )}
    meta_in = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    normalized_task = _normalized_task(task, hash_identifiers=hash_identifiers, hash_salt=hash_salt)
    attempt_id = str(data.get("attempt_id") or execution.get("attempt_id") or sha256_json({"task": normalized_task, "execution": execution, "recorded_at": data.get("recorded_at")}))
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "task_signature": sha256_json(normalized_task),
        "recorded_at": str(data.get("recorded_at") or utc_now_iso()),
        "route_id": data.get("route_id") or meta_in.get("route_id"),
        "task": normalized_task,
        "execution": {
            "model": model,
            "effort": effort,
            "product_mode": _product_mode(execution),
            "model_snapshot": execution.get("model_snapshot"),
            "success": success,
            "first_pass_success": success,
            "tests_passed": boolish(execution.get("tests_passed")),
            "user_retry": boolish(execution.get("user_retry")),
            "fallback_triggered": boolish(execution.get("fallback_triggered")),
            "final_success": boolish(execution.get("final_success")),
            "quality_score": num(execution.get("quality_score")),
            "human_edit_minutes": num(execution.get("human_edit_minutes")),
            "latency_seconds": num(execution.get("latency_seconds")),
            "cost": num(execution.get("cost")),
            "usage": usage,
        },
        "meta": dict(meta_in),
    }


def record_attempt(
    data: dict[str, Any],
    history_path: str | Path,
    *,
    hash_identifiers: bool = False,
    hash_salt: str = "",
) -> dict[str, Any]:
    row = _normalize_attempt(data, hash_identifiers=hash_identifiers, hash_salt=hash_salt)
    destination = Path(history_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with destination.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            for line in handle:
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(existing, dict) and existing.get("attempt_id") == row["attempt_id"]:
                    return {
                        "recorded": False,
                        "duplicate": True,
                        "attempt_id": row["attempt_id"],
                        "history": str(destination),
                    }
            handle.seek(0, os.SEEK_END)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return {
        "recorded": True,
        "duplicate": False,
        "attempt_id": row["attempt_id"],
        "history": str(destination),
        "recorded_at": row["recorded_at"],
        "first_pass_success": row["execution"]["first_pass_success"],
    }


def history_stats(history: list[dict[str, Any]]) -> dict[str, Any]:
    configurations: dict[str, dict[str, float]] = {}
    families: dict[str, dict[str, float]] = {}
    for row in history:
        execution = row.get("execution") if isinstance(row.get("execution"), dict) else {}
        task = row.get("task") if isinstance(row.get("task"), dict) else {}
        success = _execution_success(execution)
        model = str(execution.get("model", ""))
        effort = str(execution.get("effort", ""))
        mode = _product_mode(execution)
        if not model or not effort or success is None:
            continue
        key = f"{model}:{effort}:{mode}"
        bucket = configurations.setdefault(key, {"attempts": 0.0, "successes": 0.0, "cost": 0.0, "cost_n": 0.0, "latency": 0.0, "latency_n": 0.0})
        bucket["attempts"] += 1
        bucket["successes"] += 1 if success else 0
        cost = num(execution.get("cost"))
        latency = num(execution.get("latency_seconds"))
        if cost is not None:
            bucket["cost"] += cost
            bucket["cost_n"] += 1
        if latency is not None:
            bucket["latency"] += latency
            bucket["latency_n"] += 1
        family_key = f"{task_family(task.get('task_type'))}|{key}"
        family_bucket = families.setdefault(family_key, {"attempts": 0.0, "successes": 0.0})
        family_bucket["attempts"] += 1
        family_bucket["successes"] += 1 if success else 0
    by_configuration = {
        key: {
            "attempts": int(value["attempts"]),
            "first_pass_success_rate": round(value["successes"] / value["attempts"], 4),
            "average_cost": round(value["cost"] / value["cost_n"], 6) if value["cost_n"] else None,
            "average_latency_seconds": round(value["latency"] / value["latency_n"], 3) if value["latency_n"] else None,
        }
        for key, value in configurations.items()
    }
    by_family = {
        key: {
            "attempts": int(value["attempts"]),
            "first_pass_success_rate": round(value["successes"] / value["attempts"], 4),
        }
        for key, value in families.items()
    }
    return {"total_records": len(history), "by_configuration": by_configuration, "by_task_family_and_configuration": by_family}
