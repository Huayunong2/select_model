"""Conservative, auditable model routing engine."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from .constants import (
    ALGORITHM_VERSION,
    BASE_CAPABILITY_MARGIN,
    BASE_SOURCE_REGRET,
    DEFAULT_EFFORT_CAPACITY,
    RISK,
    RISK_MARGIN_FACTOR,
    ROUTE_SCHEMA_VERSION,
)
from .cost import estimate_task_cost
from .errors import RoutingError, ValidationError
from .evidence import build_evidence_profiles, candidate_key
from .history import history_usage_estimate, personal_calibration
from .registry import hydrate_candidate, load_model_registry, load_source_registry, registry_summary
from .task import most_informative_missing_feature, profile_text, task_pressures
from .utils import clamp, num, parse_datetime, stable_hash, utc_now, weighted_median


def _validate_route_input(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValidationError("route input must be an object")
    task = data.get("task")
    if not isinstance(task, dict):
        raise ValidationError("task must be an object")
    if not str(task.get("task_type", "")).strip():
        raise ValidationError("task.task_type is required")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValidationError("candidates must be a non-empty array")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValidationError(f"candidates[{index}] must be an object")
        if not str(candidate.get("model", "")).strip():
            raise ValidationError(f"candidates[{index}].model is required")
        if not str(candidate.get("effort", "")).strip():
            raise ValidationError(f"candidates[{index}].effort is required")


def _candidate_precheck(
    raw_candidate: dict[str, Any],
    task: dict[str, Any],
    pressure: dict[str, Any],
    model_registry: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    candidate, warnings = hydrate_candidate(raw_candidate, model_registry)
    model = str(candidate.get("model", "")).strip()
    effort = str(candidate.get("effort", "medium")).lower()
    candidate["effort"] = effort
    candidate["product_mode"] = str(
        candidate.get("product_mode", candidate.get("mode", "standard"))
    ).lower()
    mode = pressure["eligibility_mode"]
    target_runtime = str(task.get("target_runtime", "advisor")).lower()
    unknown_penalty = 0.0

    registry_entry = model_registry.get("models", {}).get(model)
    if not isinstance(registry_entry, dict):
        if mode in {"strict", "balanced"}:
            return None, warnings, "model is absent from the registry"
        unknown_penalty += 5.0
        warnings.append("model is absent from the registry; eligibility is unverified")
    else:
        runtimes = candidate.get("runtimes")
        if not isinstance(runtimes, list):
            if mode in {"strict", "balanced"}:
                return None, warnings, "candidate runtimes are unknown"
            unknown_penalty += 3.0
        elif target_runtime not in {str(item).lower() for item in runtimes}:
            return None, warnings, f"candidate is unavailable on runtime={target_runtime}"

        if target_runtime == "api":
            efforts = candidate.get("api_efforts")
            if not isinstance(efforts, list):
                if mode in {"strict", "balanced"}:
                    return None, warnings, "API effort support is unknown"
                unknown_penalty += 3.0
            elif effort not in {str(item).lower() for item in efforts}:
                return None, warnings, f"effort {effort!r} is unsupported on the API runtime"

    required = task.get("required_capabilities")
    if isinstance(required, list) and required:
        capabilities = candidate.get("capabilities")
        if not isinstance(capabilities, list):
            if mode in {"strict", "balanced"}:
                return None, warnings, "required capabilities cannot be verified"
            warnings.append("required capabilities are unverified in explore mode")
            unknown_penalty += 5.0
        else:
            available = {str(item).lower() for item in capabilities}
            missing = [str(item) for item in required if str(item).lower() not in available]
            if missing:
                return None, warnings, "missing capabilities: " + ", ".join(missing)

    context_need = num(task.get("estimated_context_tokens"))
    context_window = num(candidate.get("context_window"))
    if context_need is not None:
        if context_window is None:
            if mode in {"strict", "balanced"}:
                return None, warnings, "candidate context window is unknown"
            warnings.append("candidate context window is unverified in explore mode")
            unknown_penalty += 4.0
        elif context_need > context_window:
            return None, warnings, (
                f"estimated context {context_need:.0f} exceeds candidate window {context_window:.0f}"
            )

    output_need = num(task.get("estimated_output_tokens"))
    max_output = num(candidate.get("max_output_tokens"))
    if output_need is not None:
        if max_output is None:
            if mode == "strict":
                return None, warnings, "candidate maximum output is unknown"
            unknown_penalty += 2.0
        elif output_need > max_output:
            return None, warnings, (
                f"estimated output {output_need:.0f} exceeds candidate maximum {max_output:.0f}"
            )

    effort_capacity = num(candidate.get("effort_capacity"))
    if effort_capacity is None:
        effort_capacity = DEFAULT_EFFORT_CAPACITY.get(effort)
    if effort_capacity is None:
        return None, warnings, f"effort capacity is unknown for {effort!r}"
    candidate["effort_capacity"] = clamp(effort_capacity)
    candidate["unknown_eligibility_penalty"] = unknown_penalty
    candidate["warnings"] = warnings
    return candidate, warnings, None


def _task_latency(
    candidate: dict[str, Any],
    task: dict[str, Any],
    history: list[dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    historical = history_usage_estimate(task, candidate, history, as_of=as_of)
    if historical and num(historical.get("latency_seconds")) is not None:
        return {
            "minutes": round(float(historical["latency_seconds"]) / 60.0, 3),
            "source": "personal_history",
            "confidence": "medium",
            "effective_n": historical.get("effective_n"),
        }
    explicit = num(candidate.get("estimated_task_minutes"))
    if explicit is not None:
        return {"minutes": explicit, "source": "explicit_task_estimate", "confidence": "high"}
    proxy = num(candidate.get("minutes"))
    if proxy is not None:
        return {"minutes": proxy, "source": "benchmark_proxy", "confidence": "low"}
    return {"minutes": None, "source": "unknown", "confidence": "low"}


def _enrich_candidate(
    candidate: dict[str, Any],
    profile: dict[str, Any],
    task: dict[str, Any],
    pressure: dict[str, Any],
    history: list[dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    capacity = float(candidate["effort_capacity"])
    horizon = pressure["feature_vector"]["values"]["horizon"]
    reasoning_gap = max(0.0, pressure["reasoning"] - capacity)
    horizon_gap = max(0.0, horizon - capacity - 0.05)
    tool_gap = max(0.0, pressure["tools"] - capacity - 0.12)
    raw_effort_penalty = 22.0 * reasoning_gap + 8.0 * horizon_gap + 6.0 * tool_gap

    # Exact model+effort evidence already observed this effort configuration.
    # Applying the full generic capacity penalty again would double-count it.
    effort_penalty = raw_effort_penalty * (1.0 - min(1.0, float(profile["exact_share"])))

    evidence_penalty = 5.0 * (1.0 - float(profile["confidence_score"]))
    if pressure["reliability"] >= 0.70 and profile["confidence"] == "low":
        evidence_penalty += 3.0
    if float(profile["source_disagreement_mad"]) >= 25:
        evidence_penalty += min(4.0, (float(profile["source_disagreement_mad"]) - 25.0) / 8.0)

    route_index = max(
        0.0,
        float(profile["routing_index"])
        - effort_penalty
        - evidence_penalty
        - float(candidate.get("unknown_eligibility_penalty", 0.0)),
    )

    usage_provider = lambda current_task, current_candidate: history_usage_estimate(
        current_task,
        current_candidate,
        history,
        as_of=as_of,
    )
    output = copy.deepcopy(candidate)
    output["public_profile"] = profile
    output["raw_effort_penalty"] = round(raw_effort_penalty, 3)
    output["effort_penalty"] = round(effort_penalty, 3)
    output["evidence_penalty"] = round(evidence_penalty, 3)
    output["route_index"] = round(route_index, 3)
    output["personal"] = personal_calibration(task, candidate, history, as_of=as_of)
    output["task_cost"] = estimate_task_cost(
        candidate,
        task,
        usage_provider,
        as_of=as_of,
    )
    output["task_latency"] = _task_latency(candidate, task, history, as_of)
    return output


def _pareto(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier: list[dict[str, Any]] = []
    for candidate in candidates:
        dominated = False
        for competitor in candidates:
            if candidate is competitor:
                continue
            candidate_cost = candidate["task_cost"]["cost"]
            competitor_cost = competitor["task_cost"]["cost"]
            candidate_latency = candidate["task_latency"]["minutes"]
            competitor_latency = competitor["task_latency"]["minutes"]
            candidate_cost = float(candidate_cost) if candidate_cost is not None else float("inf")
            competitor_cost = float(competitor_cost) if competitor_cost is not None else float("inf")
            candidate_latency = float(candidate_latency) if candidate_latency is not None else float("inf")
            competitor_latency = float(competitor_latency) if competitor_latency is not None else float("inf")
            if (
                competitor_cost <= candidate_cost
                and competitor_latency <= candidate_latency
                and float(competitor["route_index"]) >= float(candidate["route_index"])
                and (
                    competitor_cost < candidate_cost
                    or competitor_latency < candidate_latency
                    or float(competitor["route_index"]) > float(candidate["route_index"])
                )
            ):
                dominated = True
                break
        if not dominated:
            frontier.append(candidate)
    return frontier


def _cost_confidence_penalty(label: str) -> float:
    return {"high": 0.0, "medium": 0.08, "low": 0.16}.get(label, 0.16)


def _choose_public_cost_guarded(
    frontier: list[dict[str, Any]],
    pressure: dict[str, Any],
) -> dict[str, Any]:
    best_quality = max(frontier, key=lambda item: float(item["route_index"]))
    best_cost = best_quality["task_cost"]["cost"]
    if best_cost is None:
        return best_quality

    risk = RISK[pressure["risk"]]
    viable: list[dict[str, Any]] = []
    for candidate in frontier:
        cost = candidate["task_cost"]["cost"]
        if cost is None:
            continue
        loss = max(0.0, float(best_quality["route_index"]) - float(candidate["route_index"]))
        if loss <= 0.75:
            viable.append(candidate)
            continue
        savings = 1.0 - float(cost) / max(float(best_cost), 1e-12)
        required = (
            0.10
            + 0.045 * loss
            + 0.18 * risk
            + _cost_confidence_penalty(str(candidate["task_cost"]["confidence"]))
        )
        if savings >= min(required, 0.85):
            viable.append(candidate)

    if not viable:
        return best_quality
    return min(
        viable,
        key=lambda item: (
            float(item["task_cost"]["cost"]),
            float(item["task_latency"]["minutes"])
            if item["task_latency"]["minutes"] is not None
            else float("inf"),
            -float(item["route_index"]),
        ),
    )


def _personal_candidate(
    frontier: list[dict[str, Any]],
    task: dict[str, Any],
    pressure: dict[str, Any],
) -> dict[str, Any] | None:
    calibrated = [item for item in frontier if item["personal"].get("available")]
    if len(calibrated) < 2:
        return None

    best_probability = max(float(item["personal"]["p_success"]) for item in calibrated)
    margin = {"low": 0.08, "medium": 0.05, "high": 0.03, "critical": 0.015}[pressure["risk"]]
    eligible = [
        item
        for item in calibrated
        if float(item["personal"]["p_success"]) >= best_probability - margin
    ]
    failure_cost = num(task.get("failure_cost")) or 0.0
    time_value = num(task.get("time_value_per_hour")) or 0.0

    def objective(candidate: dict[str, Any]) -> tuple[float, float, float]:
        probability = max(float(candidate["personal"]["p_success"]), 0.05)
        cost = candidate["task_cost"]["cost"]
        minutes = candidate["task_latency"]["minutes"]
        direct_cost = float(cost) if cost is not None else 1e9
        time_cost = (float(minutes) / 60.0) * time_value if minutes is not None else 0.0
        expected = (direct_cost + time_cost) / probability + (1.0 - probability) * failure_cost
        return expected, -probability, -float(candidate["route_index"])

    return min(eligible, key=objective)


def _profile_without_source(candidate: dict[str, Any], excluded: str) -> float | None:
    signals = candidate["public_profile"].get("source_signals", {})
    remaining = [
        (float(row["signal"]), float(row["weight"]))
        for source_id, row in signals.items()
        if source_id != excluded
    ]
    if not remaining:
        return None
    consensus = weighted_median(remaining)
    return max(
        0.0,
        consensus
        - float(candidate["effort_penalty"])
        - float(candidate["evidence_penalty"])
        - float(candidate.get("unknown_eligibility_penalty", 0.0)),
    )


def _route_stability(
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
    pressure: dict[str, Any],
) -> dict[str, Any]:
    ranking = sorted(candidates, key=lambda item: -float(item["route_index"]))
    top_gap = (
        float(ranking[0]["route_index"]) - float(ranking[1]["route_index"])
        if len(ranking) > 1
        else 100.0
    )
    selected_sources = set(selected["public_profile"].get("source_signals", {}))
    if len(ranking) > 1:
        comparison_sources = set(ranking[1]["public_profile"].get("source_signals", {}))
        union = selected_sources | comparison_sources
        overlap = len(selected_sources & comparison_sources) / len(union) if union else 0.0
    else:
        overlap = 1.0

    jackknife_results: list[bool] = []
    if len(selected_sources) >= 2:
        for source_id in selected_sources:
            recomputed: list[tuple[str, float]] = []
            for candidate in candidates:
                score = _profile_without_source(candidate, source_id)
                if score is not None:
                    recomputed.append((candidate_key(candidate), score))
            if recomputed:
                winner = max(recomputed, key=lambda item: item[1])[0]
                jackknife_results.append(winner == candidate_key(selected))
    jackknife = sum(jackknife_results) / len(jackknife_results) if jackknife_results else 0.0

    task_confidence = float(pressure["feature_vector"]["overall_confidence"])
    evidence_confidence = float(selected["public_profile"]["confidence_score"])
    gap_score = clamp(top_gap / 8.0)
    score = (
        0.25 * task_confidence
        + 0.30 * evidence_confidence
        + 0.15 * overlap
        + 0.20 * jackknife
        + 0.10 * gap_score
    )
    if len(selected_sources) < 2:
        score = min(score, 0.45)
    if selected["public_profile"]["confidence"] == "low":
        score = min(score, 0.47)

    label = "high" if score >= 0.75 else "medium" if score >= 0.50 else "low"
    return {
        "label": label,
        "score": round(score, 4),
        "top_gap": round(top_gap, 3),
        "source_overlap": round(overlap, 4),
        "jackknife_stability": round(jackknife, 4),
        "jackknife_trials": len(jackknife_results),
        "task_vector_confidence": task_confidence,
        "evidence_confidence": evidence_confidence,
    }


def route(
    data: dict[str, Any],
    *,
    history: list[dict[str, Any]] | None = None,
    model_registry: dict[str, Any] | None = None,
    source_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_route_input(data)
    history = history or []
    model_registry = model_registry or load_model_registry()
    source_registry = source_registry or load_source_registry()
    task = copy.deepcopy(data["task"])
    pressure = task_pressures(task)
    as_of = parse_datetime(data.get("as_of")) or utc_now()

    prechecked: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw_candidate in data["candidates"]:
        candidate, warnings, reason = _candidate_precheck(
            raw_candidate,
            task,
            pressure,
            model_registry,
        )
        if candidate is None:
            rejected.append(
                {
                    "model": raw_candidate.get("model"),
                    "effort": raw_candidate.get("effort"),
                    "stage": "eligibility",
                    "reason": reason,
                    "warnings": warnings,
                }
            )
        else:
            prechecked.append(candidate)
    if not prechecked:
        raise RoutingError("no candidate remains after fail-closed eligibility checks")

    strict_evidence = pressure["eligibility_mode"] == "strict"
    evidence = build_evidence_profiles(
        prechecked,
        data.get("evidence"),
        source_registry,
        pressure["task_family"],
        as_of=as_of,
        strict=strict_evidence,
    )

    minimum_sources = int(
        num(task.get("minimum_evidence_sources"))
        or (2 if pressure["eligibility_mode"] == "strict" else 1)
    )
    require_exact = bool(task.get("require_exact_evidence", strict_evidence))
    enriched: list[dict[str, Any]] = []
    for candidate in prechecked:
        profile = evidence["profiles"].get(candidate_key(candidate))
        if profile is None:
            rejected.append(
                {
                    "model": candidate.get("model"),
                    "effort": candidate.get("effort"),
                    "stage": "evidence",
                    "reason": "no usable evidence",
                }
            )
            continue
        if int(profile["source_count"]) < minimum_sources:
            rejected.append(
                {
                    "model": candidate.get("model"),
                    "effort": candidate.get("effort"),
                    "stage": "evidence",
                    "reason": (
                        f"requires >= {minimum_sources} independent evidence sources; "
                        f"found {profile['source_count']}"
                    ),
                }
            )
            continue
        if require_exact and float(profile["exact_share"]) <= 0:
            rejected.append(
                {
                    "model": candidate.get("model"),
                    "effort": candidate.get("effort"),
                    "stage": "evidence",
                    "reason": "strict task requires exact model+effort evidence",
                }
            )
            continue
        enriched.append(
            _enrich_candidate(candidate, profile, task, pressure, history, as_of)
        )
    if not enriched:
        details = "; ".join(item["reason"] for item in rejected[-5:])
        raise RoutingError(f"no candidate remains after evidence checks: {details}")

    best_index = max(float(item["route_index"]) for item in enriched)
    task_confidence = float(pressure["feature_vector"]["overall_confidence"])
    confidence_factor = 0.55 + 0.45 * task_confidence
    capability_margin = (
        BASE_CAPABILITY_MARGIN[pressure["display_difficulty"]]
        * RISK_MARGIN_FACTOR[pressure["risk"]]
        * confidence_factor
    )
    capability_margin = max(0.8, capability_margin)
    capability_floor = best_index - capability_margin

    regret_limit = (
        BASE_SOURCE_REGRET[pressure["display_difficulty"]]
        * RISK_MARGIN_FACTOR[pressure["risk"]]
        * (0.70 + 0.30 * task_confidence)
    )
    regret_limit = max(10.0, regret_limit)

    eligible: list[dict[str, Any]] = []
    for candidate in enriched:
        if float(candidate["route_index"]) < capability_floor:
            continue
        worst_regret = candidate["public_profile"]["source_regret"].get("worst")
        if worst_regret is not None and float(worst_regret) > regret_limit:
            continue
        if float(candidate["effort_penalty"]) > 5.5:
            continue
        eligible.append(candidate)
    if not eligible:
        # Fail safe: quality-first, never widen the cost window after all guards fail.
        eligible = [max(enriched, key=lambda item: float(item["route_index"]))]

    frontier = _pareto(eligible) or eligible
    public_selected = _choose_public_cost_guarded(frontier, pressure)
    personal_selected = _personal_candidate(frontier, task, pressure)
    selected = public_selected
    selection_mode = "conservative_public_rank_evidence+task_cost"

    if personal_selected is not None:
        if public_selected["personal"].get("available"):
            selected = personal_selected
            selection_mode = "personal_calibrated_expected_total_cost"
        else:
            lower_bound = float(personal_selected["personal"]["interval_90"][0])
            threshold = {"low": 0.65, "medium": 0.75, "high": 0.85, "critical": 0.92}[pressure["risk"]]
            not_materially_weaker = (
                float(personal_selected["route_index"])
                >= float(public_selected["route_index"]) - capability_margin / 2.0
            )
            if lower_bound >= threshold and not_materially_weaker:
                selected = personal_selected
                selection_mode = "personal_calibrated_override_with_conservative_lower_bound"

    stronger = sorted(
        [
            item
            for item in enriched
            if float(item["route_index"]) > float(selected["route_index"]) + 1.0
        ],
        key=lambda item: (
            -float(item["route_index"]),
            float(item["task_cost"]["cost"])
            if item["task_cost"]["cost"] is not None
            else float("inf"),
        ),
    )
    fallback = stronger[0] if stronger else None
    stability = _route_stability(selected, enriched, pressure)
    missing_feature = most_informative_missing_feature(pressure)
    needs_more_context = (
        pressure["risk"] in {"high", "critical"}
        and stability["label"] == "low"
        and task_confidence < 0.55
    )

    handoff = {
        "model": selected.get("model"),
        "reasoning_effort": selected.get("effort"),
        "product_mode": selected.get("product_mode", "standard"),
        "plan": bool(pressure["plan_recommended"]),
        "target_runtime": str(task.get("target_runtime", "advisor")).lower(),
        "risk": pressure["risk"],
        "required_capabilities": task.get("required_capabilities", []),
    }

    ranking = sorted(enriched, key=lambda item: -float(item["route_index"]))
    model_registry_summary = registry_summary(model_registry)
    source_registry_summary = registry_summary(source_registry)
    audit_payload = {
        "task": task,
        "as_of": as_of.isoformat(),
        "selected": candidate_key(selected),
        "model_registry": model_registry_summary,
        "source_registry": source_registry_summary,
        "evidence_panels": evidence["panel_count"],
    }
    route_id = "route_" + stable_hash(audit_payload)[:20]
    return {
        "schema_version": ROUTE_SCHEMA_VERSION,
        "route_id": route_id,
        "algorithm_version": ALGORITHM_VERSION,
        "generated_at": as_of.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "task": pressure,
        "task_input": task,
        "task_summary": profile_text(pressure),
        "selection_mode": selection_mode,
        "selected": selected,
        "fallback": fallback,
        "pareto_frontier": frontier,
        "ranking": ranking,
        "rejected": rejected,
        "evidence_report": {
            "accepted_records": evidence["accepted_records"],
            "ranked_records": evidence["ranked_records"],
            "panel_count": evidence["panel_count"],
            "rejected": evidence["rejected"],
            "warnings": evidence["warnings"],
        },
        "guards": {
            "best_route_index": round(best_index, 3),
            "capability_margin": round(capability_margin, 3),
            "capability_floor": round(capability_floor, 3),
            "source_regret_limit": round(regret_limit, 3),
            "minimum_evidence_sources": minimum_sources,
            "require_exact_evidence": require_exact,
        },
        "route_stability": stability,
        "needs_more_context": needs_more_context,
        "most_informative_missing_feature": missing_feature if needs_more_context else None,
        "handoff": handoff,
        "audit": {
            "input_fingerprint": stable_hash(audit_payload),
            "model_registry": model_registry_summary,
            "source_registry": source_registry_summary,
        },
        "warnings": [
            "Public evidence is a routing prior, not a common ability scale.",
            "P(success) is emitted only from sufficient similar personal outcomes.",
            "Benchmark-average cost remains a low-confidence proxy.",
        ],
    }


def choose(
    data: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
    model_registry: dict[str, Any] | None = None,
    source_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias for :func:`route`."""
    return route(
        data,
        history=history,
        model_registry=model_registry,
        source_registry=source_registry,
    )


def format_route_markdown(result: dict[str, Any]) -> str:
    selected = result["selected"]
    fallback = result.get("fallback")
    lines = [
        f"**任务：** {result['task_summary']}",
        f"**推荐：** `{selected['model']}` / `{selected['effort']}`",
        (
            "**依据：** 路由指数 "
            f"{selected['route_index']:.2f}；证据置信度 "
            f"{selected['public_profile']['confidence']}；成本来源 "
            f"{selected['task_cost']['source']}。"
        ),
        f"**计划：** {'开启' if result['handoff']['plan'] else '关闭'}",
        f"**稳定性：** {result['route_stability']['label']} ({result['route_stability']['score']:.2f})",
    ]
    if fallback:
        lines.append(f"**回退：** `{fallback['model']}` / `{fallback['effort']}`")
    personal = selected.get("personal", {})
    if personal.get("available"):
        lines.append(
            "**个人校准：** "
            f"首轮成功率 {float(personal['p_success']):.1%}，"
            f"90% 区间 {float(personal['interval_90'][0]):.1%}–{float(personal['interval_90'][1]):.1%}。"
        )
    else:
        lines.append(f"**个人校准：** {personal.get('status', 'unavailable')}。")
    if result.get("needs_more_context"):
        lines.append(
            "**最值得补充的信息：** "
            f"`{result.get('most_informative_missing_feature')}`。"
        )
    return "\n\n".join(lines) + "\n"
