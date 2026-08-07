"""Versioned model and evidence-source registry helpers."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .constants import DEFAULT_EFFORT_CAPACITY, DEFAULT_MODEL_REGISTRY, DEFAULT_SOURCE_REGISTRY
from .utils import load_json, num, parse_datetime


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _string_list(value: Any, label: str, *, non_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value):
        qualifier = "non-empty " if non_empty else ""
        raise ValueError(f"{label} must be a {qualifier}string list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{label} must contain only non-empty strings")
    return [item.strip() for item in value]


def _https_url(value: Any, label: str, *, required: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError(f"{label} is required")
        return None
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an HTTPS URL without embedded credentials")
    return text


def validate_model_registry(registry: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if registry.get("schema_version") != 1:
        raise ValueError("model registry schema_version must be 1")
    if parse_datetime(registry.get("updated_at")) is None:
        raise ValueError("model registry updated_at must be timezone-aware ISO-8601")

    models = _object(registry.get("models"), "model registry models")
    if not models:
        raise ValueError("model registry is empty")

    for raw_model_id, payload in models.items():
        model_id = str(raw_model_id).strip()
        if not model_id or model_id != raw_model_id:
            raise ValueError("model registry keys must be non-empty strings without surrounding whitespace")
        model = _object(payload, f"model {model_id}")
        runtimes = _string_list(model.get("runtimes"), f"{model_id}.runtimes", non_empty=True)
        efforts_value = model.get("api_efforts")
        efforts = _string_list(efforts_value, f"{model_id}.api_efforts") if efforts_value is not None else []
        if "api" in {item.lower() for item in runtimes} and not efforts:
            raise ValueError(f"{model_id}.api_efforts must be non-empty when API runtime is enabled")

        effort_capacity = _object(model.get("effort_capacity"), f"{model_id}.effort_capacity")
        for effort, raw_capacity in effort_capacity.items():
            capacity = num(raw_capacity)
            if capacity is None or not 0 <= capacity <= 1:
                raise ValueError(f"{model_id}.effort_capacity.{effort} must be in [0, 1]")
        for effort in efforts:
            if effort not in effort_capacity:
                raise ValueError(f"{model_id}.effort_capacity is missing API effort {effort!r}")

        context_window = num(model.get("context_window"))
        max_output = num(model.get("max_output_tokens"))
        if context_window is None or context_window <= 0:
            raise ValueError(f"{model_id}.context_window must be positive")
        if max_output is None or max_output <= 0:
            raise ValueError(f"{model_id}.max_output_tokens must be positive")
        if max_output > context_window:
            warnings.append(f"{model_id}.max_output_tokens exceeds context_window; verify provider semantics")

        _string_list(model.get("capabilities"), f"{model_id}.capabilities")
        pricing = _object(model.get("pricing"), f"{model_id}.pricing")
        if parse_datetime(pricing.get("observed_at")) is None:
            raise ValueError(f"{model_id}.pricing.observed_at must be timezone-aware ISO-8601")
        _https_url(pricing.get("source_url"), f"{model_id}.pricing.source_url")
        currency = str(pricing.get("currency", "")).strip()
        if not currency:
            raise ValueError(f"{model_id}.pricing.currency is required")

        for field in ("input_per_million", "cached_input_per_million", "output_per_million"):
            value = num(pricing.get(field))
            if value is None or value < 0:
                raise ValueError(f"{model_id}.pricing.{field} must be non-negative")
        write_multiplier = num(pricing.get("cache_write_multiplier"))
        write_rate = num(pricing.get("cache_write_per_million"))
        if write_multiplier is None and write_rate is None:
            raise ValueError(
                f"{model_id}.pricing needs cache_write_multiplier or cache_write_per_million"
            )
        if write_multiplier is not None and write_multiplier < 0:
            raise ValueError(f"{model_id}.pricing.cache_write_multiplier must be non-negative")
        if write_rate is not None and write_rate < 0:
            raise ValueError(f"{model_id}.pricing.cache_write_per_million must be non-negative")

        threshold = num(pricing.get("long_context_threshold"))
        input_multiplier = num(pricing.get("long_context_input_multiplier"))
        output_multiplier = num(pricing.get("long_context_output_multiplier"))
        if threshold is not None:
            if threshold <= 0:
                raise ValueError(f"{model_id}.pricing.long_context_threshold must be positive")
            if input_multiplier is None or input_multiplier <= 0:
                raise ValueError(
                    f"{model_id}.pricing.long_context_input_multiplier must be positive"
                )
            if output_multiplier is None or output_multiplier <= 0:
                raise ValueError(
                    f"{model_id}.pricing.long_context_output_multiplier must be positive"
                )

    aliases = registry.get("aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("model registry aliases must be an object")
    for raw_alias, target in aliases.items():
        alias = str(raw_alias).strip()
        if not alias or alias != raw_alias:
            raise ValueError("model aliases must be non-empty strings without surrounding whitespace")
        if not isinstance(target, str) or target not in models:
            raise ValueError(f"alias {alias!r} points to unknown model {target!r}")
    return warnings


def validate_source_registry(registry: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if registry.get("schema_version") != 1:
        raise ValueError("source registry schema_version must be 1")
    if parse_datetime(registry.get("updated_at")) is None:
        raise ValueError("source registry updated_at must be timezone-aware ISO-8601")
    if registry.get("unknown_sources", "reject") != "reject":
        warnings.append("production configuration should reject unknown evidence sources")

    sources = _object(registry.get("sources"), "source registry sources")
    if not sources:
        raise ValueError("source registry is empty")
    for raw_source_id, payload in sources.items():
        source_id = str(raw_source_id).strip()
        if not source_id or source_id != raw_source_id:
            raise ValueError("source registry keys must be non-empty strings without whitespace")
        source = _object(payload, f"source {source_id}")
        trust = num(source.get("trust"))
        ttl = num(source.get("ttl_hours"))
        half_life = num(source.get("freshness_half_life_hours"))
        sample_scale = num(source.get("sample_scale"))
        if trust is None or not 0 <= trust <= 1:
            raise ValueError(f"{source_id}.trust must be in [0, 1]")
        for name, value in (
            ("ttl_hours", ttl),
            ("freshness_half_life_hours", half_life),
            ("sample_scale", sample_scale),
        ):
            if value is None or value <= 0:
                raise ValueError(f"{source_id}.{name} must be positive")

        relevance = _object(source.get("task_relevance"), f"{source_id}.task_relevance")
        if not relevance:
            raise ValueError(f"{source_id}.task_relevance must not be empty")
        for family, raw_value in relevance.items():
            value = num(raw_value)
            if value is None or not 0 <= value <= 1:
                raise ValueError(f"{source_id}.task_relevance.{family} must be in [0, 1]")

        _string_list(
            source.get("accepted_metrics"),
            f"{source_id}.accepted_metrics",
            non_empty=True,
        )
        official_urls = source.get("official_urls")
        if official_urls is None:
            warnings.append(f"{source_id}.official_urls is missing")
        else:
            for index, url in enumerate(
                _string_list(official_urls, f"{source_id}.official_urls", non_empty=True)
            ):
                _https_url(url, f"{source_id}.official_urls[{index}]")
    return warnings


def load_model_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry = load_json(path or DEFAULT_MODEL_REGISTRY)
    validate_model_registry(registry)
    return registry


def load_source_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry = load_json(path or DEFAULT_SOURCE_REGISTRY)
    validate_source_registry(registry)
    return registry


def resolve_model_id(model_id: str, registry: dict[str, Any]) -> str:
    aliases = registry.get("aliases") if isinstance(registry.get("aliases"), dict) else {}
    return str(aliases.get(model_id, model_id))


def hydrate_candidate(
    candidate: dict[str, Any], registry: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    output = copy.deepcopy(candidate)
    warnings: list[str] = []
    requested = str(output.get("model", "")).strip()
    resolved = resolve_model_id(requested, registry)
    defaults = registry.get("models", {}).get(resolved)
    if not isinstance(defaults, dict):
        warnings.append(f"model {requested!r} is not in the registry")
        return output, warnings
    output["model"] = resolved
    output.setdefault("requested_model", requested)
    for key in (
        "display_name",
        "snapshot",
        "runtimes",
        "api_efforts",
        "context_window",
        "max_output_tokens",
        "capabilities",
        "effort_capacity",
        "pricing",
    ):
        if key not in output:
            output[key] = copy.deepcopy(defaults.get(key))
        elif (
            key == "pricing"
            and isinstance(defaults.get(key), dict)
            and isinstance(output.get(key), dict)
        ):
            merged = copy.deepcopy(defaults[key])
            merged.update(output[key])
            output[key] = merged
    effort = str(output.get("effort", "medium")).lower()
    output["effort"] = effort
    capacity = output.get("effort_capacity")
    if isinstance(capacity, dict):
        output["effort_capacity"] = num(capacity.get(effort))
    elif num(capacity) is None:
        configured = defaults.get("effort_capacity")
        output["effort_capacity"] = (
            num(configured.get(effort))
            if isinstance(configured, dict)
            else DEFAULT_EFFORT_CAPACITY.get(effort)
        )
    return output, warnings


def registry_summary(registry: dict[str, Any]) -> dict[str, Any]:
    """Return a content-addressed summary for either supported registry kind."""
    encoded = json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    collections: dict[str, list[str]] = {}
    for name in ("models", "sources"):
        value = registry.get(name)
        if isinstance(value, dict):
            collections[name] = sorted(str(item) for item in value)
    kind = next(iter(collections), "unknown")
    return {
        "schema_version": registry.get("schema_version"),
        "updated_at": registry.get("updated_at"),
        "kind": kind,
        **collections,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }
