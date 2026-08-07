"""Automation primitives for evidence stores, generic JSON adapters, and registry sync."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .evidence import validate_evidence_envelope
from .registry import validate_model_registry, validate_source_registry
from .utils import (
    atomic_write_json,
    deep_get,
    iso_now,
    iter_jsonl,
    load_json_any,
    parse_datetime,
    sha256_bytes,
    stable_hash,
    utc_now,
    validate_public_https_url,
)

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target before urllib follows it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_source_bytes(
    location: str,
    *,
    timeout_seconds: int = 30,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> tuple[bytes, str]:
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme:
        validate_public_https_url(location)
        request = urllib.request.Request(
            location,
            headers={"Accept": "application/json", "User-Agent": "select-model/4.0"},
        )
        opener = urllib.request.build_opener(_ValidatedRedirectHandler())
        with opener.open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            validate_public_https_url(final_url)
            payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError(f"download exceeds {max_bytes} bytes")
        return payload, final_url

    path = Path(location).expanduser()
    payload = path.read_bytes()
    if len(payload) > max_bytes:
        raise ValueError(f"file exceeds {max_bytes} bytes")
    return payload, path.resolve().as_uri()


def _extract_records(payload: Any, path: str | None) -> list[Any]:
    selected = deep_get(payload, path, payload) if path else payload
    if not isinstance(selected, list):
        raise ValueError("mapping spec records_path must resolve to an array")
    return selected


def collect_from_mapping_spec(
    spec: dict[str, Any],
    source_registry: dict[str, Any],
    *,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Convert a public JSON endpoint or local snapshot into evidence envelopes.

    This deliberately avoids brittle HTML scraping. A small declarative mapping
    adapts stable JSON APIs/snapshots to the evidence envelope.
    """
    if not isinstance(spec, dict):
        raise ValueError("collector spec must be an object")
    source_id = str(spec.get("source_id", "")).strip()
    if source_id not in source_registry.get("sources", {}):
        raise ValueError(f"source_id is not allowlisted: {source_id}")
    location = str(spec.get("location") or spec.get("url") or "").strip()
    if not location:
        raise ValueError("collector spec requires location")

    raw, fetched_from = _read_source_bytes(location, timeout_seconds=timeout_seconds)
    source_url = str(spec.get("source_url") or fetched_from)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"collector source is not valid UTF-8 JSON: {exc}") from exc

    rows = _extract_records(payload, spec.get("records_path"))
    fields = spec.get("fields") if isinstance(spec.get("fields"), dict) else {}
    metric = spec.get("metric") if isinstance(spec.get("metric"), dict) else {}
    metric_name = str(metric.get("name", "")).strip()
    if not metric_name:
        raise ValueError("collector spec metric.name is required")
    higher_is_better = metric.get("higher_is_better")
    if not isinstance(higher_is_better, bool):
        raise ValueError("collector spec metric.higher_is_better must be boolean")

    model_map = spec.get("model_map") if isinstance(spec.get("model_map"), dict) else {}
    observed_default = spec.get("observed_at") or iso_now()
    raw_hash = sha256_bytes(raw)
    snapshot_id = str(spec.get("snapshot_id") or raw_hash[:16])
    match = str(spec.get("match", "exact")).lower()
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            skipped.append({"index": index, "reason": "row is not an object"})
            continue
        raw_model = deep_get(row, str(fields.get("model", "model")))
        model = str(model_map.get(str(raw_model), raw_model or "")).strip()
        effort = str(deep_get(row, str(fields.get("effort", "effort")), spec.get("effort", ""))).strip().lower()
        value = deep_get(row, str(fields.get("value", "value")))
        observed_at = deep_get(
            row,
            str(fields.get("observed_at", "observed_at")),
            observed_default,
        )
        sample_size = deep_get(row, str(fields.get("sample_size", "sample_size")))
        ci_half_width = deep_get(row, str(fields.get("ci_half_width", "ci_half_width")))
        if not model or value is None:
            skipped.append({"index": index, "reason": "model or metric value is missing"})
            continue

        record = {
            "schema_version": "1.0",
            "source_id": source_id,
            "observed_at": observed_at,
            "subject": {
                "model": model,
                "effort": effort,
                "snapshot": deep_get(row, str(fields.get("snapshot", "snapshot"))),
            },
            "metric": {
                "name": metric_name,
                "value": value,
                "higher_is_better": higher_is_better,
                "version": metric.get("version", "1"),
            },
            "match": match,
            "sample_size": sample_size,
            "ci_half_width": ci_half_width,
            "harness": deep_get(row, str(fields.get("harness", "harness")), spec.get("harness", "unspecified")),
            "snapshot_id": snapshot_id,
            "source_url": source_url,
            "raw_sha256": raw_hash,
        }
        errors, warnings = validate_evidence_envelope(record, source_registry, strict=bool(spec.get("strict", True)))
        if errors:
            skipped.append({"index": index, "reason": "; ".join(errors), "warnings": warnings})
            continue
        records.append(record)

    return {
        "schema_version": "1.0",
        "collected_at": iso_now(),
        "source_id": source_id,
        "source_url": source_url,
        "fetched_from": fetched_from,
        "raw_sha256": raw_hash,
        "records": records,
        "skipped": skipped,
    }


def _extract_envelopes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("records"), list):
            return [item for item in value["records"] if isinstance(item, dict)]
        return [value]
    raise ValueError("evidence input must be an object or array")


def import_evidence(
    value: Any,
    store_path: str | Path,
    source_registry: dict[str, Any],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    records = _extract_envelopes(value)
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        errors, warnings = validate_evidence_envelope(record, source_registry, strict=strict)
        if errors:
            rejected.append({"index": index, "errors": errors, "warnings": warnings})
        else:
            cloned = dict(record)
            cloned.setdefault("imported_at", iso_now())
            cloned.setdefault("evidence_id", stable_hash(record))
            valid.append(cloned)

    target = Path(store_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    imported = 0
    duplicates = 0
    with target.open("a+", encoding="utf-8") as handle:
        lock_module = None
        try:
            import fcntl as lock_module  # type: ignore[no-redef]

            lock_module.flock(handle.fileno(), lock_module.LOCK_EX)
        except (ImportError, OSError):
            lock_module = None
        handle.seek(0)
        known: set[str] = set()
        for line in handle:
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(existing, dict):
                known.add(str(existing.get("evidence_id") or stable_hash(existing)))
        handle.seek(0, os.SEEK_END)
        for record in valid:
            evidence_id = str(record["evidence_id"])
            if evidence_id in known:
                duplicates += 1
                continue
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            known.add(evidence_id)
            imported += 1
        handle.flush()
        os.fsync(handle.fileno())
        if lock_module is not None:
            lock_module.flock(handle.fileno(), lock_module.LOCK_UN)

    return {
        "store": str(target),
        "input_records": len(records),
        "imported": imported,
        "duplicates": duplicates,
        "rejected": rejected,
    }


def load_evidence_store(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    invalid = 0
    for line_number, row, error in iter_jsonl(source):
        if error:
            invalid += 1
            warnings.append(f"line {line_number}: {error}")
        elif row is not None:
            records.append(row)
    return {
        "path": str(source),
        "records": records,
        "invalid_lines": invalid,
        "warnings": warnings,
    }


def latest_evidence(
    records: list[dict[str, Any]],
    source_registry: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    as_of = as_of or utc_now()
    latest: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for record in records:
        source_id = str(record.get("source_id", ""))
        source = source_registry.get("sources", {}).get(source_id)
        observed = parse_datetime(record.get("observed_at"))
        if not isinstance(source, dict) or observed is None:
            continue
        age = (as_of - observed).total_seconds() / 3600.0
        if age < -5 / 60 or age > float(source["ttl_hours"]):
            continue
        key = (
            source_id,
            str(deep_get(record, "subject.model", "")),
            str(deep_get(record, "subject.effort", "")),
            str(deep_get(record, "metric.name", "")),
            str(record.get("harness", "")),
        )
        current = latest.get(key)
        current_time = parse_datetime(current.get("observed_at")) if current else None
        if current is None or current_time is None or observed > current_time:
            latest[key] = record
    return sorted(
        latest.values(),
        key=lambda item: (
            str(item.get("source_id", "")),
            str(deep_get(item, "subject.model", "")),
            str(deep_get(item, "subject.effort", "")),
        ),
    )


def build_route_input(
    task: dict[str, Any],
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise ValueError("task must be an object")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty array")
    return {
        "schema_version": "4.0",
        "as_of": as_of or iso_now(),
        "task": task,
        "candidates": candidates,
        "evidence": evidence,
    }


def sync_registry(
    kind: str,
    location: str,
    output_path: str | Path,
    *,
    expected_sha256: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    raw, source_url = _read_source_bytes(location, timeout_seconds=timeout_seconds)
    digest = sha256_bytes(raw)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise ValueError(f"registry SHA-256 mismatch: expected {expected_sha256}, got {digest}")
    try:
        registry = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"registry is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(registry, dict):
        raise ValueError("registry must be an object")
    if kind == "models":
        warnings = validate_model_registry(registry)
    elif kind == "sources":
        warnings = validate_source_registry(registry)
    else:
        raise ValueError("registry kind must be models or sources")
    atomic_write_json(output_path, registry)
    return {
        "kind": kind,
        "output": str(Path(output_path)),
        "source_url": source_url,
        "sha256": digest,
        "warnings": warnings,
    }
