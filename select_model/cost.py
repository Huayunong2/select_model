"""Task-level cost estimation with explicit pricing provenance."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .utils import age_hours, num


def compute_priced_cost(pricing: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    errors: list[str] = []

    input_rate = num(pricing.get("input_per_million"))
    cached_rate = num(pricing.get("cached_input_per_million"))
    output_rate = num(pricing.get("output_per_million"))
    write_rate = num(pricing.get("cache_write_per_million"))
    write_multiplier = num(pricing.get("cache_write_multiplier"))
    for name, value in (
        ("input_per_million", input_rate),
        ("cached_input_per_million", cached_rate),
        ("output_per_million", output_rate),
    ):
        if value is None or value < 0:
            errors.append(f"pricing.{name} must be non-negative")
    if write_rate is not None and write_rate < 0:
        errors.append("pricing.cache_write_per_million must be non-negative")
    if write_multiplier is not None and write_multiplier < 0:
        errors.append("pricing.cache_write_multiplier must be non-negative")

    total_input = num(usage.get("input_tokens"))
    uncached = num(usage.get("uncached_input_tokens"))
    cached = num(usage.get("cached_input_tokens"))
    cache_write = num(usage.get("cache_write_tokens"))
    output = num(usage.get("output_tokens"))
    tool_cost = num(usage.get("tool_cost"))

    cached = 0.0 if cached is None else cached
    cache_write = 0.0 if cache_write is None else cache_write
    tool_cost = 0.0 if tool_cost is None else tool_cost
    for name, value in (
        ("input_tokens", total_input),
        ("uncached_input_tokens", uncached),
        ("cached_input_tokens", cached),
        ("cache_write_tokens", cache_write),
        ("output_tokens", output),
        ("tool_cost", tool_cost),
    ):
        if value is not None and value < 0:
            errors.append(f"usage.{name} must be non-negative")

    if uncached is None:
        if total_input is None:
            errors.append("usage requires input_tokens or uncached_input_tokens")
        else:
            if cached + cache_write > total_input:
                errors.append("cached_input_tokens + cache_write_tokens cannot exceed input_tokens")
            uncached = max(0.0, total_input - cached - cache_write)
    elif total_input is not None and uncached + cached + cache_write > total_input + 1e-9:
        errors.append(
            "uncached_input_tokens + cached_input_tokens + cache_write_tokens cannot exceed input_tokens"
        )
    if output is None:
        errors.append("usage requires billed output_tokens, including billed reasoning output")

    if cache_write > 0 and write_rate is None:
        if write_multiplier is None or input_rate is None:
            errors.append("cache-write pricing is unknown")
        else:
            write_rate = input_rate * write_multiplier
            notes.append(f"cache-write rate derived from {write_multiplier:g}x input multiplier")
    elif write_rate is None:
        write_rate = 0.0

    prompt_tokens = (
        total_input
        if total_input is not None
        else float(uncached or 0.0) + cached + cache_write
    )
    threshold = num(pricing.get("long_context_threshold"))
    input_multiplier = 1.0
    output_multiplier = 1.0
    long_context = False
    if threshold is not None:
        if threshold <= 0:
            errors.append("pricing.long_context_threshold must be positive")
        elif prompt_tokens > threshold:
            long_context = True
            raw_input_multiplier = num(pricing.get("long_context_input_multiplier"))
            raw_output_multiplier = num(pricing.get("long_context_output_multiplier"))
            if raw_input_multiplier is None or raw_input_multiplier <= 0:
                errors.append("long-context input multiplier is missing or invalid")
            else:
                input_multiplier = raw_input_multiplier
            if raw_output_multiplier is None or raw_output_multiplier <= 0:
                errors.append("long-context output multiplier is missing or invalid")
            else:
                output_multiplier = raw_output_multiplier
            notes.append(f"long-context multipliers applied above {threshold:g} prompt tokens")

    if errors:
        return {
            "cost": None,
            "currency": pricing.get("currency", "USD"),
            "errors": errors,
            "notes": notes,
            "long_context": long_context,
        }

    assert input_rate is not None
    assert cached_rate is not None
    assert output_rate is not None
    assert write_rate is not None
    assert uncached is not None
    assert output is not None
    components = {
        "uncached_input": uncached / 1_000_000 * input_rate * input_multiplier,
        "cached_input": cached / 1_000_000 * cached_rate * input_multiplier,
        "cache_write": cache_write / 1_000_000 * write_rate * input_multiplier,
        "output": output / 1_000_000 * output_rate * output_multiplier,
        "tools": tool_cost,
    }
    return {
        "cost": round(sum(components.values()), 8),
        "currency": pricing.get("currency", "USD"),
        "components": {key: round(value, 8) for key, value in components.items()},
        "long_context": long_context,
        "input_multiplier": input_multiplier,
        "output_multiplier": output_multiplier,
        "errors": [],
        "notes": notes,
    }


def _freshness(
    result: dict[str, Any],
    pricing: dict[str, Any],
    source: str,
    confidence: str,
    as_of: datetime,
) -> dict[str, Any]:
    enriched = dict(result)
    age = age_hours(pricing.get("observed_at"), as_of)
    enriched.update(
        {
            "source": source,
            "pricing_observed_at": pricing.get("observed_at"),
            "pricing_age_hours": round(age, 2) if age is not None else None,
        }
    )
    if age is None or age > 24 * 90:
        enriched["confidence"] = "low"
        enriched.setdefault("notes", []).append("pricing is missing or older than 90 days")
    elif age > 24 * 30 and confidence == "high":
        enriched["confidence"] = "medium"
        enriched.setdefault("notes", []).append("pricing is older than 30 days")
    else:
        enriched["confidence"] = confidence
    return enriched


def estimate_task_cost(
    candidate: dict[str, Any],
    task: dict[str, Any],
    history_usage_provider: Callable[
        [dict[str, Any], dict[str, Any]], dict[str, Any] | None
    ]
    | None,
    *,
    as_of: datetime,
) -> dict[str, Any]:
    pricing = candidate.get("pricing") if isinstance(candidate.get("pricing"), dict) else None
    usage = task.get("usage_estimate") if isinstance(task.get("usage_estimate"), dict) else None
    if pricing and usage:
        result = compute_priced_cost(pricing, usage)
        if result.get("cost") is not None:
            return _freshness(result, pricing, "pricing+task_usage", "high", as_of)
    if pricing and history_usage_provider:
        historical = history_usage_provider(task, candidate)
        if historical:
            result = compute_priced_cost(pricing, historical)
            if result.get("cost") is not None:
                output = _freshness(result, pricing, "pricing+personal_usage", "medium", as_of)
                output["effective_n"] = historical.get("effective_n")
                return output
    explicit = num(candidate.get("estimated_task_cost"))
    if explicit is not None and explicit >= 0:
        return {
            "cost": explicit,
            "currency": candidate.get("currency", "USD"),
            "source": "explicit_task_estimate",
            "confidence": "high",
            "notes": [],
            "errors": [],
        }
    proxy = num(candidate.get("cost"))
    if proxy is not None and proxy >= 0:
        return {
            "cost": proxy,
            "currency": candidate.get("currency", "USD"),
            "source": "benchmark_proxy",
            "confidence": "low",
            "notes": ["benchmark proxy is not the cost of this task"],
            "errors": [],
        }
    return {
        "cost": None,
        "currency": pricing.get("currency", "USD") if pricing else "USD",
        "source": "unknown",
        "confidence": "low",
        "notes": [],
        "errors": ["no task-level cost estimate is available"],
    }
