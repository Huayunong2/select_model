from __future__ import annotations

import copy
from typing import Any

from select_model.registry import load_model_registry, load_source_registry

AS_OF = "2026-08-07T00:00:00Z"
MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]


def envelope(
    model: str,
    source_id: str,
    value: float,
    *,
    effort: str = "medium",
    metric: str = "score",
    observed_at: str = "2026-08-06T00:00:00Z",
    match: str = "exact",
    complete: bool = True,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "source_id": source_id,
        "observed_at": observed_at,
        "subject": {"model": model, "effort": effort, "snapshot": model},
        "metric": {
            "name": metric,
            "value": value,
            "higher_is_better": True,
            "version": "test-v1",
        },
        "match": match,
        "sample_size": 1000,
        "ci_half_width": 2.0,
        "harness": "unit-test",
        "snapshot_id": snapshot_id or f"{source_id}-snapshot",
        "source_url": f"https://example.com/{source_id}",
        "raw_sha256": "a" * 64,
    }
    if not complete:
        for key in ("observed_at", "sample_size", "ci_half_width", "snapshot_id", "source_url", "raw_sha256"):
            record.pop(key, None)
        record.pop("match", None)
        record["subject"].pop("effort", None)
    return record


def route_input(
    *,
    risk: str = "medium",
    sources: tuple[str, ...] = ("codexradar", "artificial_analysis"),
    values: dict[str, list[float]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    values = values or {
        "codexradar": [90, 82, 75],
        "artificial_analysis": [88, 83, 78],
    }
    records = []
    for source in sources:
        metric = "score"
        for model, value in zip(MODELS, values[source]):
            records.append(envelope(model, source, value, metric=metric))
    return {
        "schema_version": "4.0",
        "as_of": AS_OF,
        "task": {
            "task_type": "coding.refactor",
            "risk": risk,
            "target_runtime": "api",
            "features": {
                "reasoning": "medium",
                "context": "medium",
                "test_quality": "high",
                "detectability": "high",
                "rollback": "high",
            },
            "usage_estimate": {
                "input_tokens": 100_000,
                "cached_input_tokens": 50_000,
                "cache_write_tokens": 0,
                "output_tokens": 10_000,
            },
        },
        "candidates": candidates or [{"model": model, "effort": "medium"} for model in MODELS],
        "evidence": records,
    }


def model_registry() -> dict[str, Any]:
    return copy.deepcopy(load_model_registry())


def source_registry() -> dict[str, Any]:
    return copy.deepcopy(load_source_registry())
