"""Local diagnostics for an installation or repository checkout."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .automation import load_evidence_store
from .constants import ALGORITHM_VERSION, PACKAGE_ROOT, PROJECT_ROOT
from .history import load_history
from .registry import (
    load_model_registry,
    load_source_registry,
    registry_summary,
    validate_model_registry,
    validate_source_registry,
)
from .utils import age_hours, parse_datetime, utc_now


def run_doctor(
    *,
    model_registry_path: str | None = None,
    source_registry_path: str | None = None,
    history_path: str | None = None,
    evidence_store_path: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    as_of = utc_now()

    try:
        models = load_model_registry(model_registry_path)
        registry_warnings = validate_model_registry(models)
        summary = registry_summary(models)
        checks.append(
            {
                "name": "model_registry",
                "ok": True,
                "models": summary["models"],
                "sha256": summary["sha256"],
                "warnings": registry_warnings,
            }
        )
        stale_prices: list[dict[str, Any]] = []
        for model_id, model in models["models"].items():
            pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
            age = age_hours(pricing.get("observed_at"), as_of)
            if age is None or age > 24 * 90 or age < -24:
                stale_prices.append({"model": model_id, "age_hours": round(age, 2) if age is not None else None})
        checks.append(
            {
                "name": "pricing_freshness",
                "ok": not stale_prices,
                "stale_or_unknown": stale_prices,
                "policy_days": 90,
            }
        )
    except Exception as exc:  # pragma: no cover - exact parser failure varies
        checks.append({"name": "model_registry", "ok": False, "error": str(exc)})

    try:
        sources = load_source_registry(source_registry_path)
        source_warnings = validate_source_registry(sources)
        source_summary = registry_summary(sources)
        checks.append(
            {
                "name": "source_registry",
                "ok": True,
                "sources": source_summary["sources"],
                "sha256": source_summary["sha256"],
                "warnings": source_warnings,
            }
        )
        source_age = age_hours(sources.get("updated_at"), as_of)
        source_fresh = source_age is not None and -24 <= source_age <= 24 * 180
        checks.append(
            {
                "name": "source_registry_freshness",
                "ok": source_fresh,
                "age_hours": round(source_age, 2) if source_age is not None else None,
                "policy_days": 180,
            }
        )
    except Exception as exc:  # pragma: no cover
        checks.append({"name": "source_registry", "ok": False, "error": str(exc)})

    if history_path:
        history = load_history(history_path)
        checks.append(
            {
                "name": "history",
                "ok": history["invalid_lines"] == 0,
                "records": len(history["rows"]),
                "invalid_lines": history["invalid_lines"],
                "warnings": history["warnings"],
                "path": history["path"],
            }
        )

    if evidence_store_path:
        evidence = load_evidence_store(evidence_store_path)
        checks.append(
            {
                "name": "evidence_store",
                "ok": evidence["invalid_lines"] == 0,
                "records": len(evidence["records"]),
                "invalid_lines": evidence["invalid_lines"],
                "warnings": evidence["warnings"],
                "path": evidence["path"],
            }
        )

    repository_checkout = (PROJECT_ROOT / "SKILL.md").exists()
    if repository_checkout:
        required_files = [
            "SKILL.md",
            "README.md",
            "LICENSE",
            "pyproject.toml",
            "config/models.json",
            "config/sources.json",
            "select_model/router.py",
            "tests",
        ]
        missing = [item for item in required_files if not (PROJECT_ROOT / item).exists()]
        layout_root = PROJECT_ROOT
        layout_mode = "repository"
    else:
        required_files = ["__init__.py", "router.py", "data/models.json", "data/sources.json"]
        missing = [item for item in required_files if not (PACKAGE_ROOT / item).exists()]
        layout_root = PACKAGE_ROOT
        layout_mode = "installed_package"
    checks.append(
        {
            "name": "project_layout",
            "ok": not missing,
            "missing": missing,
            "root": str(layout_root),
            "mode": layout_mode,
        }
    )
    checks.append(
        {
            "name": "python",
            "ok": sys.version_info >= (3, 11),
            "version": sys.version.split()[0],
            "minimum": "3.11",
        }
    )
    checks.append(
        {
            "name": "openai_api_key",
            "ok": bool(os.environ.get("OPENAI_API_KEY")),
            "required_only_for_live_dispatch": True,
        }
    )

    required_names = {"model_registry", "source_registry", "project_layout", "python"}
    if strict:
        required_names.update({"pricing_freshness", "source_registry_freshness"})
    if history_path:
        required_names.add("history")
    if evidence_store_path:
        required_names.add("evidence_store")
    status = {str(check["name"]): bool(check.get("ok")) for check in checks}
    healthy = all(status.get(name, False) for name in required_names)
    return {
        "version": ALGORITHM_VERSION,
        "healthy": healthy,
        "checks": checks,
    }
