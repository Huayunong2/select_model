"""Task-profile facade with routing-specific names and diagnostics."""
from __future__ import annotations

from typing import Any

from .constants import FEATURE_DECISION_IMPACT
from .task_profile import feature_vector, infer_task_profile, task_family
from .task_profile import task_pressures as _task_pressures


def task_pressures(task: dict[str, Any]) -> dict[str, Any]:
    result = _task_pressures(task)
    result["eligibility_mode"] = result.pop("policy_mode")
    return result


def most_informative_missing_feature(pressure: dict[str, Any]) -> str | None:
    missing = pressure.get("feature_vector", {}).get("missing", [])
    if not missing:
        return None
    return max(missing, key=lambda item: FEATURE_DECISION_IMPACT.get(item, 0.0))


def profile_text(pressure: dict[str, Any]) -> str:
    return (
        f"{pressure['task_family']} / {pressure['display_difficulty']} / "
        f"risk={pressure['risk']} / eligibility={pressure['eligibility_mode']} / "
        f"task-confidence={pressure['feature_vector']['overall_confidence']:.3f}"
    )


__all__ = [
    "feature_vector",
    "infer_task_profile",
    "most_informative_missing_feature",
    "profile_text",
    "task_family",
    "task_pressures",
]
