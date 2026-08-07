"""Lightweight schema validators used by the CLI and integrations."""
from __future__ import annotations

from typing import Any

from .constants import ROUTE_SCHEMA_VERSION
from .evidence import validate_evidence_envelope
from .utils import parse_datetime


def validate_route_input(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["route input must be an object"]
    version = str(data.get("schema_version", ROUTE_SCHEMA_VERSION))
    if version not in {"1.0", ROUTE_SCHEMA_VERSION}:
        errors.append(f"unsupported route schema_version: {version}")
    if data.get("as_of") is not None and parse_datetime(data.get("as_of")) is None:
        errors.append("as_of must be ISO-8601")

    task = data.get("task")
    if not isinstance(task, dict):
        errors.append("task must be an object")
    else:
        if not str(task.get("task_type", "")).strip():
            errors.append("task.task_type is required")
        if str(task.get("risk", "medium")).lower() not in {"low", "medium", "high", "critical"}:
            errors.append("task.risk must be low, medium, high, or critical")

    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        errors.append("candidates must be a non-empty array")
    else:
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                errors.append(f"candidates[{index}] must be an object")
                continue
            if not str(candidate.get("model", "")).strip():
                errors.append(f"candidates[{index}].model is required")
            if not str(candidate.get("effort", "")).strip():
                errors.append(f"candidates[{index}].effort is required")
    return errors


def validate_context_manifest(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["context manifest must be an object"]
    for field in ("required_capabilities", "portable_capabilities", "file_ids", "input_history"):
        if field in data and not isinstance(data[field], list):
            errors.append(f"{field} must be an array")
    if not any(key in data for key in ("input", "prompt", "input_history")):
        errors.append("context requires input, prompt, or input_history")
    if data.get("attach_file_ids") and not isinstance(data.get("file_ids"), list):
        errors.append("attach_file_ids=true requires file_ids array")
    return errors


__all__ = [
    "validate_context_manifest",
    "validate_evidence_envelope",
    "validate_route_input",
]
