"""Evidence normalization and robust source-internal rank aggregation.

The router never arithmetic-averages heterogeneous benchmark values. Each source
panel is ranked internally, then candidate rank percentiles are combined with a
weighted median. Public evidence remains a routing prior, never a task-success
probability.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from .constants import EVIDENCE_SCHEMA_VERSION, MATCH_FACTOR
from .utils import (
    age_hours,
    clamp,
    confidence_label,
    deep_get,
    num,
    parse_datetime,
    weighted_mean,
    weighted_median,
)


def candidate_key(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('model', '')}:{str(candidate.get('effort', 'medium')).lower()}"


def _metric_from_legacy(source_id: str, payload: dict[str, Any]) -> tuple[str, float, bool] | None:
    preferences = {
        "codexradar": ("iq", "pass_rate", "score"),
        "arena_agent": ("percentile", "rank"),
        "arena_code": ("percentile", "rank"),
        "arena_webdev": ("percentile", "rank"),
        "arena_text": ("percentile", "rank"),
        "artificial_analysis": ("score", "index"),
        "swe_bench_live": ("resolved", "score"),
    }
    for metric_name in preferences.get(source_id, ("score", "index", "percentile", "rank")):
        value = num(payload.get(metric_name))
        if value is None:
            continue
        if metric_name in {"pass_rate", "resolved"} and 0 <= value <= 1:
            value *= 100.0
        return metric_name, value, metric_name != "rank"
    return None


def _legacy_records(
    candidate: dict[str, Any],
    payload: Any,
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    records: list[dict[str, Any]] = []
    for source_id, source_payload in payload.items():
        if not isinstance(source_payload, dict):
            source_payload = {"score": source_payload}
        metric = _metric_from_legacy(str(source_id), source_payload)
        if metric is None:
            continue
        metric_name, metric_value, higher_is_better = metric
        observed_at = source_payload.get("observed_at")
        if observed_at is None:
            legacy_age = num(source_payload.get("age_hours"))
            if legacy_age is not None:
                observed_at = (as_of - timedelta(hours=max(legacy_age, 0.0))).isoformat()
        match = str(source_payload.get("match", "proxy")).lower()
        if match not in MATCH_FACTOR:
            match = "proxy"
        records.append(
            {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "source_id": str(source_id),
                "observed_at": observed_at,
                "subject": {
                    "model": candidate.get("model"),
                    "effort": candidate.get("effort"),
                    "snapshot": candidate.get("snapshot"),
                },
                "metric": {
                    "name": metric_name,
                    "value": metric_value,
                    "higher_is_better": higher_is_better,
                    "version": source_payload.get("metric_version", "legacy"),
                },
                "match": match,
                "sample_size": source_payload.get("sample_size"),
                "ci_half_width": source_payload.get("ci_half_width"),
                "harness": source_payload.get("harness", "legacy"),
                "snapshot_id": source_payload.get("snapshot_id", "legacy"),
                "source_url": source_payload.get("source_url"),
                "raw_sha256": source_payload.get("raw_sha256"),
                "legacy": True,
                "panel_id": source_payload.get("panel_id", "legacy"),
            }
        )
    return records


def collect_evidence_records(
    candidates: list[dict[str, Any]],
    global_evidence: Any,
    *,
    as_of: datetime,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(global_evidence, list):
        records.extend(item for item in global_evidence if isinstance(item, dict))
    elif isinstance(global_evidence, dict):
        global_records = global_evidence.get("records")
        if isinstance(global_records, list):
            records.extend(item for item in global_records if isinstance(item, dict))

    for candidate in candidates:
        payload = candidate.get("evidence")
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    cloned = dict(item)
                    cloned.setdefault("candidate_hint", candidate_key(candidate))
                    records.append(cloned)
        elif isinstance(payload, dict):
            records.extend(_legacy_records(candidate, payload, as_of=as_of))
    return records


def validate_evidence_envelope(
    record: dict[str, Any],
    source_registry: dict[str, Any],
    *,
    strict: bool = False,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return ["evidence record must be an object"], warnings

    version = str(record.get("schema_version", EVIDENCE_SCHEMA_VERSION))
    if version != EVIDENCE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {EVIDENCE_SCHEMA_VERSION}")

    source_id = str(record.get("source_id", "")).strip()
    source = source_registry.get("sources", {}).get(source_id)
    if not source_id:
        errors.append("source_id is required")
    elif not isinstance(source, dict):
        errors.append(f"unknown source_id: {source_id}")

    observed = parse_datetime(record.get("observed_at"))
    if observed is None:
        if strict:
            errors.append("strict evidence requires observed_at")
        else:
            warnings.append("observed_at missing; freshness weight will be heavily reduced")

    model = str(deep_get(record, "subject.model", "")).strip()
    effort = str(deep_get(record, "subject.effort", "")).strip().lower()
    model_snapshot = str(deep_get(record, "subject.snapshot", "")).strip()
    if not model:
        errors.append("subject.model is required")

    match = str(record.get("match", "proxy")).lower()
    if match not in MATCH_FACTOR:
        warnings.append("match missing/invalid; downgraded to proxy")
    if match == "exact" and not effort:
        errors.append("exact evidence requires subject.effort")
    if strict and match == "exact" and not model_snapshot:
        errors.append("strict exact evidence requires subject.snapshot")
    if strict and record.get("legacy") and match == "exact":
        errors.append("strict routing rejects legacy exact evidence")

    metric_name = str(deep_get(record, "metric.name", "")).strip()
    metric_value = num(deep_get(record, "metric.value"))
    direction = deep_get(record, "metric.higher_is_better")
    if not metric_name:
        errors.append("metric.name is required")
    if metric_value is None:
        errors.append("metric.value must be finite")
    if not isinstance(direction, bool):
        errors.append("metric.higher_is_better must be boolean")
    if isinstance(source, dict):
        accepted = {str(item) for item in source.get("accepted_metrics", [])}
        if metric_name and metric_name not in accepted:
            errors.append(f"metric {metric_name!r} is not accepted for source {source_id}")

    sample_size = num(record.get("sample_size"))
    if record.get("sample_size") is not None and (sample_size is None or sample_size < 0):
        errors.append("sample_size must be non-negative")
    elif sample_size is None:
        if strict:
            errors.append("strict evidence requires sample_size")
        else:
            warnings.append("sample_size missing; sample weight reduced")

    ci = num(record.get("ci_half_width"))
    if record.get("ci_half_width") is not None and (ci is None or ci < 0):
        errors.append("ci_half_width must be non-negative")
    elif ci is None:
        warnings.append("uncertainty interval missing; uncertainty weight reduced")

    provenance_fields = ("snapshot_id", "source_url", "raw_sha256")
    for field in provenance_fields:
        if not record.get(field):
            if strict:
                errors.append(f"strict evidence requires {field}")
            else:
                warnings.append(f"{field} missing; provenance confidence reduced")
    source_url = str(record.get("source_url", "")).strip()
    if source_url:
        parsed_url = urlparse(source_url)
        if (
            parsed_url.scheme.lower() != "https"
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
        ):
            errors.append("source_url must be an HTTPS URL without embedded credentials")
    raw_hash = str(record.get("raw_sha256", ""))
    if raw_hash and (len(raw_hash) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in raw_hash)):
        errors.append("raw_sha256 must be a 64-character hexadecimal SHA-256")
    return errors, warnings


def _normalise_record(
    record: dict[str, Any],
    candidates_by_key: dict[str, dict[str, Any]],
    source_registry: dict[str, Any],
    task_family: str,
    *,
    as_of: datetime,
    strict: bool,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    errors, warnings = validate_evidence_envelope(record, source_registry, strict=strict)
    if errors:
        return None, errors, warnings

    source_id = str(record["source_id"])
    source = source_registry["sources"][source_id]
    model = str(deep_get(record, "subject.model", "")).strip()
    effort = str(deep_get(record, "subject.effort", "")).strip().lower()
    key = f"{model}:{effort or 'medium'}"
    candidate = candidates_by_key.get(key)
    if candidate is None:
        # Family/proxy evidence may omit effort. Attach only when exactly one
        # candidate uses the referenced model; ambiguity is rejected.
        matches = [item for item in candidates_by_key.values() if str(item.get("model")) == model]
        if len(matches) == 1:
            candidate = matches[0]
            key = candidate_key(candidate)
        else:
            return None, [f"evidence subject does not match one candidate unambiguously: {model}:{effort}"], warnings

    match = str(record.get("match", "proxy")).lower()
    if match not in MATCH_FACTOR:
        match = "proxy"
    if record.get("legacy") and strict and match == "exact":
        return None, ["legacy exact evidence is not allowed in strict mode"], warnings
    if match == "exact" and effort != str(candidate.get("effort", "medium")).lower():
        return None, ["exact evidence effort does not match candidate effort"], warnings
    evidence_snapshot = str(deep_get(record, "subject.snapshot", "")).strip()
    candidate_snapshot = str(candidate.get("snapshot", "")).strip()
    if (
        match == "exact"
        and evidence_snapshot
        and candidate_snapshot
        and evidence_snapshot != candidate_snapshot
    ):
        return None, ["exact evidence model snapshot does not match candidate snapshot"], warnings

    observed = parse_datetime(record.get("observed_at"))
    source_age = age_hours(record.get("observed_at"), as_of)
    if source_age is not None and source_age < -5 / 60:
        return None, ["observed_at is in the future"], warnings
    ttl = float(source["ttl_hours"])
    if source_age is not None and source_age > ttl:
        return None, [f"evidence is stale ({source_age:.1f}h > TTL {ttl:.1f}h)"], warnings

    freshness = 0.25
    if observed is not None and source_age is not None:
        half_life = float(source["freshness_half_life_hours"])
        freshness = max(0.20, math.exp(-math.log(2.0) * max(source_age, 0.0) / half_life))

    sample_size = num(record.get("sample_size"))
    sample_factor = 0.55
    if sample_size is not None:
        scale = float(source["sample_scale"])
        sample_factor = 0.60 + 0.40 * (1.0 - math.exp(-sample_size / scale))

    ci = num(record.get("ci_half_width"))
    uncertainty = 0.75 if ci is None else clamp(1.0 - ci / 30.0, 0.35, 1.0)

    binding = 1.0 if match == "exact" and effort else 0.72 if match == "family" else 0.45
    provenance_parts = [
        1.0 if record.get("snapshot_id") else 0.70,
        1.0 if str(record.get("source_url", "")).startswith("https://") else 0.72,
        1.0 if record.get("raw_sha256") else 0.72,
        0.65 if record.get("legacy") else 1.0,
    ]
    provenance = sum(provenance_parts) / len(provenance_parts)
    metadata_confidence = (
        (1.0 if observed is not None else 0.25)
        + sample_factor
        + uncertainty
        + binding
        + provenance
    ) / 5.0

    relevance = num(source.get("task_relevance", {}).get(task_family))
    if relevance is None:
        relevance = num(source.get("task_relevance", {}).get("general")) or 0.30
    trust = float(source["trust"])
    weight = (
        trust
        * relevance
        * MATCH_FACTOR[match]
        * freshness
        * sample_factor
        * uncertainty
        * provenance
    )

    metric_name = str(deep_get(record, "metric.name"))
    metric_version = str(deep_get(record, "metric.version", "unspecified"))
    harness = str(record.get("harness", "unspecified"))
    snapshot_id = str(record.get("snapshot_id") or record.get("panel_id") or "unspecified")
    higher_is_better = bool(deep_get(record, "metric.higher_is_better"))
    panel_key = "|".join(
        (source_id, metric_name, metric_version, harness, snapshot_id, "high" if higher_is_better else "low")
    )
    normalised = {
        "candidate_key": key,
        "source_id": source_id,
        "panel_key": panel_key,
        "metric_name": metric_name,
        "metric_value": float(deep_get(record, "metric.value")),
        "higher_is_better": higher_is_better,
        "weight": round(weight, 8),
        "metadata_confidence": round(metadata_confidence, 6),
        "match": match,
        "observed_at": record.get("observed_at"),
        "age_hours": round(source_age, 3) if source_age is not None else None,
        "sample_size": sample_size,
        "legacy": bool(record.get("legacy")),
        "source_url": record.get("source_url"),
    }
    return normalised, [], warnings


def _rank_panel(rows: list[dict[str, Any]], candidate_count: int) -> list[dict[str, Any]]:
    """Attach source-internal rank percentiles, averaging tied ranks."""
    # Deduplicate repeated candidate snapshots in one panel by preferring higher
    # evidence weight, then newer data.
    deduplicated: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = deduplicated.get(row["candidate_key"])
        if existing is None or float(row["weight"]) > float(existing["weight"]):
            deduplicated[row["candidate_key"]] = row
    unique = list(deduplicated.values())
    if not unique:
        return []

    higher = bool(unique[0]["higher_is_better"])
    unique.sort(key=lambda item: float(item["metric_value"]), reverse=higher)
    total = len(unique)
    coverage = total / max(candidate_count, 1)
    output: list[dict[str, Any]] = []
    index = 0
    while index < total:
        end = index + 1
        while end < total and math.isclose(
            float(unique[end]["metric_value"]),
            float(unique[index]["metric_value"]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        if total == 1:
            signal = 50.0
            discrimination = 0.35
        else:
            signal = 100.0 * (total - average_rank) / (total - 1.0)
            discrimination = 1.0
        panel_factor = (0.60 + 0.40 * coverage) * discrimination
        for row in unique[index:end]:
            enriched = dict(row)
            enriched.update(
                {
                    "rank": round(average_rank, 3),
                    "panel_size": total,
                    "panel_coverage": round(coverage, 4),
                    "rank_signal": round(signal, 4),
                    "effective_weight": round(float(row["weight"]) * panel_factor, 8),
                }
            )
            output.append(enriched)
        index = end
    return output


def _candidate_profile(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_source[row["source_id"]].append(row)

    source_signals: dict[str, dict[str, Any]] = {}
    for source_id, source_rows in by_source.items():
        weights = [float(item["effective_weight"]) for item in source_rows]
        source_signal = weighted_median(
            (float(item["rank_signal"]), float(item["effective_weight"]))
            for item in source_rows
        )
        source_weight = min(1.0, sum(weights))
        metadata = weighted_mean(
            (float(item["metadata_confidence"]), max(float(item["effective_weight"]), 1e-9))
            for item in source_rows
        )
        exact_weight = sum(
            float(item["effective_weight"])
            for item in source_rows
            if item["match"] == "exact"
        )
        total_weight = sum(weights)
        source_signals[source_id] = {
            "signal": round(source_signal, 3),
            "weight": round(source_weight, 6),
            "metadata_confidence": round(metadata, 4),
            "exact_share": round(exact_weight / total_weight, 4) if total_weight else 0.0,
            "panels": len(source_rows),
        }

    consensus = weighted_median(
        (float(row["signal"]), float(row["weight"]))
        for row in source_signals.values()
    )
    source_values = [float(row["signal"]) for row in source_signals.values()]
    median = statistics.median(source_values)
    mad = statistics.median(abs(value - median) for value in source_values) if len(source_values) > 1 else 0.0

    total_source_weight = sum(float(row["weight"]) for row in source_signals.values())
    exact_share = (
        sum(float(row["weight"]) * float(row["exact_share"]) for row in source_signals.values())
        / total_source_weight
        if total_source_weight
        else 0.0
    )
    metadata_confidence = (
        sum(float(row["weight"]) * float(row["metadata_confidence"]) for row in source_signals.values())
        / total_source_weight
        if total_source_weight
        else 0.0
    )
    source_count = len(source_signals)
    source_coverage = min(1.0, source_count / 3.0)
    disagreement = clamp(1.0 - mad / 40.0)
    effective_sources = sum(min(1.0, float(row["weight"])) for row in source_signals.values())
    confidence_score = (
        0.25 * source_coverage
        + 0.20 * exact_share
        + 0.20 * metadata_confidence
        + 0.20 * disagreement
        + 0.15 * min(1.0, effective_sources / 2.5)
    )
    if source_count == 1:
        confidence_score = min(confidence_score, 0.45)
    elif source_count == 2:
        confidence_score = min(confidence_score, 0.78)

    return {
        "routing_index": round(consensus, 3),
        "aggregation": "weighted median of source-internal candidate rank percentiles",
        "confidence": confidence_label(confidence_score),
        "confidence_score": round(confidence_score, 4),
        "source_count": source_count,
        "effective_sources": round(effective_sources, 3),
        "exact_share": round(exact_share, 4),
        "metadata_confidence": round(metadata_confidence, 4),
        "source_disagreement_mad": round(mad, 3),
        "source_signals": source_signals,
        "panel_signals": rows,
        "warning": "Public evidence is a routing prior, not P(success | this task).",
    }


def build_evidence_profiles(
    candidates: list[dict[str, Any]],
    global_evidence: Any,
    source_registry: dict[str, Any],
    task_family: str,
    *,
    as_of: datetime,
    strict: bool = False,
) -> dict[str, Any]:
    """Validate evidence, rank candidates inside panels, and build profiles."""
    candidates_by_key = {candidate_key(candidate): candidate for candidate in candidates}
    raw_records = collect_evidence_records(candidates, global_evidence, as_of=as_of)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []

    for index, record in enumerate(raw_records):
        normalised, errors, record_warnings = _normalise_record(
            record,
            candidates_by_key,
            source_registry,
            task_family,
            as_of=as_of,
            strict=strict,
        )
        warnings.extend(f"evidence[{index}]: {warning}" for warning in record_warnings)
        if normalised is None:
            rejected.append({"index": index, "errors": errors, "source_id": record.get("source_id")})
        else:
            accepted.append(normalised)

    panels: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in accepted:
        panels[record["panel_key"]].append(record)

    ranked_rows: list[dict[str, Any]] = []
    for panel_rows in panels.values():
        ranked_rows.extend(_rank_panel(panel_rows, len(candidates)))

    rows_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ranked_rows:
        rows_by_candidate[row["candidate_key"]].append(row)

    profiles = {
        key: _candidate_profile(rows_by_candidate.get(key, []))
        for key in candidates_by_key
    }

    # Source regret uses only comparable rank panels and is therefore not a raw
    # cross-benchmark score difference.
    best_by_panel: dict[str, float] = {}
    for row in ranked_rows:
        best_by_panel[row["panel_key"]] = max(
            best_by_panel.get(row["panel_key"], float("-inf")),
            float(row["rank_signal"]),
        )
    for key, profile in profiles.items():
        if profile is None:
            continue
        regrets: list[tuple[float, float, str]] = []
        for row in profile["panel_signals"]:
            regret = max(0.0, best_by_panel[row["panel_key"]] - float(row["rank_signal"]))
            regrets.append((regret, float(row["effective_weight"]), str(row["source_id"])))
        if regrets:
            worst = max(regrets, key=lambda item: item[0])
            profile["source_regret"] = {
                "weighted_median": round(weighted_median((gap, weight) for gap, weight, _ in regrets), 3),
                "worst": round(worst[0], 3),
                "worst_source": worst[2],
            }
        else:
            profile["source_regret"] = {"weighted_median": None, "worst": None, "worst_source": None}

    return {
        "profiles": profiles,
        "accepted_records": len(accepted),
        "ranked_records": len(ranked_rows),
        "panel_count": len(panels),
        "rejected": rejected,
        "warnings": warnings,
    }
