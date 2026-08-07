"""Project-wide constants.

Mutable facts such as model availability, capabilities, context limits, and prices
belong in the versioned JSON registries under ``config/``. This module contains
only stable routing policy defaults.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_VERSION = "4.0.0"
ALGORITHM_VERSION = "select-model-v4"
ROUTE_SCHEMA_VERSION = "4.0"
EVIDENCE_SCHEMA_VERSION = "1.0"
HISTORY_SCHEMA_VERSION = 2

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
_REPOSITORY_MODEL_REGISTRY = PROJECT_ROOT / "config" / "models.json"
_REPOSITORY_SOURCE_REGISTRY = PROJECT_ROOT / "config" / "sources.json"
DEFAULT_MODEL_REGISTRY = (
    _REPOSITORY_MODEL_REGISTRY
    if _REPOSITORY_MODEL_REGISTRY.exists()
    else PACKAGE_ROOT / "data" / "models.json"
)
DEFAULT_SOURCE_REGISTRY = (
    _REPOSITORY_SOURCE_REGISTRY
    if _REPOSITORY_SOURCE_REGISTRY.exists()
    else PACKAGE_ROOT / "data" / "sources.json"
)
DEFAULT_HISTORY_PATH = Path.home() / ".select-model" / "history.jsonl"
DEFAULT_EVIDENCE_STORE = Path.home() / ".select-model" / "evidence.jsonl"

FEATURES = (
    "reasoning",
    "context",
    "unfamiliarity",
    "tools",
    "browser",
    "cross_file",
    "test_quality",
    "detectability",
    "rollback",
    "ambiguity",
    "horizon",
)

FEATURE_DEFAULTS = {
    "reasoning": (0.45, 0.25),
    "context": (0.35, 0.25),
    "unfamiliarity": (0.50, 0.20),
    "tools": (0.30, 0.25),
    "browser": (0.00, 0.35),
    "cross_file": (0.35, 0.25),
    "test_quality": (0.55, 0.20),
    "detectability": (0.60, 0.20),
    "rollback": (0.70, 0.20),
    "ambiguity": (0.35, 0.25),
    "horizon": (0.35, 0.25),
}

BUCKETS = {
    "very_low": 0.12,
    "very-low": 0.12,
    "low": 0.28,
    "medium": 0.50,
    "mid": 0.50,
    "high": 0.72,
    "very_high": 0.90,
    "very-high": 0.90,
}

FEATURE_CONFIDENCE_WEIGHTS = {
    "reasoning": 1.20,
    "context": 1.00,
    "unfamiliarity": 0.70,
    "tools": 0.80,
    "browser": 0.40,
    "cross_file": 1.00,
    "test_quality": 0.80,
    "detectability": 1.00,
    "rollback": 1.00,
    "ambiguity": 0.90,
    "horizon": 1.20,
}

FEATURE_DECISION_IMPACT = {
    "reasoning": 1.20,
    "horizon": 1.20,
    "context": 1.05,
    "cross_file": 1.05,
    "detectability": 1.00,
    "rollback": 1.00,
    "ambiguity": 0.95,
    "test_quality": 0.90,
    "tools": 0.85,
    "unfamiliarity": 0.75,
    "browser": 0.45,
}

RISK = {"low": 0.15, "medium": 0.40, "high": 0.72, "critical": 1.00}
RISK_MARGIN_FACTOR = {
    "low": 1.00,
    "medium": 0.85,
    "high": 0.65,
    "critical": 0.45,
}

BASE_CAPABILITY_MARGIN = {
    "easy": 9.0,
    "normal": 6.5,
    "hard": 4.0,
    "very_hard": 2.5,
    "extreme": 1.2,
}

# Regret is measured on source-internal rank-percentile panels, not on mixed raw
# benchmark scales. The wider range reflects that rank-percentile gap.
BASE_SOURCE_REGRET = {
    "easy": 70.0,
    "normal": 60.0,
    "hard": 50.0,
    "very_hard": 40.0,
    "extreme": 30.0,
}

DEFAULT_EFFORT_CAPACITY = {
    "none": 0.12,
    "low": 0.30,
    "medium": 0.50,
    "high": 0.68,
    "xhigh": 0.83,
    "max": 0.95,
}

MATCH_FACTOR = {"exact": 1.00, "family": 0.55, "proxy": 0.30}
VALID_ELIGIBILITY_MODES = {"explore", "balanced", "strict"}
VALID_RUNTIMES = {"advisor", "api", "chatgpt", "codex", "work", "atlas"}
HOST_ONLY_CAPABILITIES = {
    "repo",
    "local_repo",
    "workspace",
    "tool_state",
    "browser_session",
    "ide_state",
    "shell_state",
}

PASSTHROUGH_RESPONSE_FIELDS = {
    "instructions",
    "previous_response_id",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "max_output_tokens",
    "text",
    "metadata",
    "store",
}

# Compatibility constants used by the task-profile implementation.
POLICY_MODES = {"auto", "explore", "balanced", "strict"}
TASK_FAMILY_ALIASES = {
    "code": "coding",
    "coding": "coding",
    "software": "coding",
    "debug": "coding",
    "refactor": "coding",
    "programming": "coding",
    "dev": "coding",
    "research": "research",
    "browse": "research",
    "web": "research",
    "writing": "writing",
    "write": "writing",
    "content": "writing",
    "analysis": "analysis",
    "data": "analysis",
    "math": "analysis",
    "agent": "agent",
    "automation": "agent",
    "workflow": "agent",
}
