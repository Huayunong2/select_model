"""Small standard-library utilities used across the package."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "pass", "passed", "success"}:
            return True
        if normalized in {"0", "false", "no", "n", "fail", "failed", "failure"}:
            return False
    return None


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_iso() -> str:
    return iso_now()


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def age_hours(observed_at: Any, as_of: datetime) -> float | None:
    observed = parse_datetime(observed_at)
    if observed is None:
        return None
    return (as_of - observed).total_seconds() / 3600.0


def load_json(path: str | Path) -> dict[str, Any]:
    value = load_json_any(path)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def load_json_any(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}") from exc


def dump_json(value: Any, *, compact: bool = False) -> str:
    if compact:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def atomic_write_text(path: str | Path, content: str) -> None:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(path, dump_json(value))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return stable_hash(value)


def deep_get(value: Any, dotted: str, default: Any = None) -> Any:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def weighted_mean(items: Iterable[tuple[float, float]]) -> float:
    clean = [(float(value), max(0.0, float(weight))) for value, weight in items]
    total = sum(weight for _, weight in clean)
    if total <= 0:
        raise ValueError("weighted_mean requires positive total weight")
    return sum(value * weight for value, weight in clean) / total


def weighted_median(items: Iterable[tuple[float, float]]) -> float:
    clean = sorted(
        (float(value), max(0.0, float(weight)))
        for value, weight in items
        if math.isfinite(float(value)) and math.isfinite(float(weight))
    )
    if not clean:
        raise ValueError("weighted_median requires data")
    total = sum(weight for _, weight in clean)
    if total <= 0:
        values = [value for value, _ in clean]
        middle = len(values) // 2
        return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0
    accumulated = 0.0
    for value, weight in clean:
        accumulated += weight
        if accumulated >= total / 2.0:
            return value
    return clean[-1][0]


def confidence_label(value: float) -> str:
    return "high" if value >= 0.72 else "medium" if value >= 0.48 else "low"


def iter_jsonl(path: str | Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    source = Path(path).expanduser()
    if not source.exists():
        return
    for line_number, line in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            yield line_number, None, str(exc)
            continue
        if not isinstance(value, dict):
            yield line_number, None, "JSONL row must be an object"
            continue
        yield line_number, value, None


def redact(value: Any, *, max_string: int = 80) -> Any:
    if isinstance(value, str):
        return f"<redacted string: {len(value)} chars>" if len(value) > max_string else "<redacted>"
    if isinstance(value, list):
        return [redact(item, max_string=max_string) for item in value]
    if isinstance(value, dict):
        safe = {"type", "role", "model", "effort", "server_label", "file_id", "name", "tool_choice"}
        return {
            key: item if key in safe and not isinstance(item, (dict, list)) else redact(item, max_string=max_string)
            for key, item in value.items()
        }
    return value


def sanitize_identifier(value: Any, *, salt: str = "") -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return "sha256:" + hashlib.sha256((salt + "\0" + text).encode("utf-8")).hexdigest()


def validate_public_https_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    """Reject common SSRF destinations before an outbound collector request."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("remote URL must use HTTPS and include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("remote URL must not include credentials")
    host = parsed.hostname.lower().rstrip(".")
    if allowed_hosts is not None and host not in {item.lower().rstrip(".") for item in allowed_hosts}:
        raise ValueError(f"remote host is not allowlisted: {host}")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"remote hostname cannot be resolved: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError(f"remote hostname resolves to a non-public address: {ip}")
    return url
