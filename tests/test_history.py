from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from select_model.history import (
    build_attempt_from_artifacts,
    load_history,
    personal_calibration,
    record_attempt,
)


TASK = {
    "task_type": "coding.refactor",
    "risk": "medium",
    "repo_id": "repo-a",
    "environment_id": "linux",
    "features": {
        "reasoning": "medium",
        "context": "medium",
        "cross_file": "medium",
        "test_quality": "high",
    },
}
CANDIDATE = {
    "model": "gpt-5.6-luna",
    "effort": "medium",
    "snapshot": "gpt-5.6-luna",
}


def attempt(attempt_id: str, recorded_at: str, success: bool = True) -> dict:
    return {
        "attempt_id": attempt_id,
        "recorded_at": recorded_at,
        "task": TASK,
        "execution": {
            "model": "gpt-5.6-luna",
            "model_snapshot": "gpt-5.6-luna",
            "effort": "medium",
            "first_pass_success": success,
            "final_success": success,
            "tests_passed": success,
            "latency_seconds": 10,
            "usage": {"input_tokens": 1000, "output_tokens": 100},
        },
    }


class HistoryTests(unittest.TestCase):
    def test_duplicate_attempt_id_is_not_written_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            first = record_attempt(attempt("same", "2026-08-06T00:00:00Z"), path)
            second = record_attempt(attempt("same", "2026-08-06T00:00:00Z"), path)
            self.assertTrue(first["recorded"])
            self.assertTrue(second["duplicate"])
            self.assertEqual(len(load_history(path)["rows"]), 1)

    def test_recent_records_have_more_effective_weight(self) -> None:
        now = datetime(2026, 8, 7, tzinfo=UTC)
        recent = [
            {
                "recorded_at": "2026-08-06T00:00:00Z",
                "task": TASK,
                "execution": {
                    "model": "gpt-5.6-luna",
                    "effort": "medium",
                    "model_snapshot": "gpt-5.6-luna",
                    "first_pass_success": True,
                },
            }
            for _ in range(12)
        ]
        old = [dict(row, recorded_at="2024-01-01T00:00:00Z") for row in recent]
        recent_result = personal_calibration(TASK, CANDIDATE, recent, as_of=now)
        old_result = personal_calibration(TASK, CANDIDATE, old, as_of=now)
        self.assertGreater(recent_result["effective_n"], old_result["effective_n"])

    def test_artifact_builder_reads_nested_cached_tokens_and_product_mode(self) -> None:
        built = build_attempt_from_artifacts(
            {
                "route_id": "route_test",
                "task_input": TASK,
                "selected": {
                    "model": "gpt-5.6-luna",
                    "snapshot": "gpt-5.6-luna",
                    "effort": "medium",
                    "product_mode": "standard",
                },
            },
            {
                "id": "resp_test",
                "usage": {
                    "input_tokens": 1000,
                    "input_tokens_details": {"cached_tokens": 600},
                    "output_tokens": 100,
                },
            },
            {"first_pass_success": True, "final_success": True},
        )
        execution = built["execution"]
        self.assertEqual(execution["product_mode"], "standard")
        self.assertEqual(execution["usage"]["cached_input_tokens"], 600)

    def test_legacy_mode_alias_matches_product_mode(self) -> None:
        rows = [
            {
                "recorded_at": "2026-08-06T00:00:00Z",
                "task": TASK,
                "execution": {
                    "model": "gpt-5.6-luna",
                    "effort": "medium",
                    "mode": "standard",
                    "model_snapshot": "gpt-5.6-luna",
                    "first_pass_success": True,
                },
            }
            for _ in range(12)
        ]
        result = personal_calibration(
            TASK,
            {**CANDIDATE, "product_mode": "standard"},
            rows,
            as_of=datetime(2026, 8, 7, tzinfo=UTC),
        )
        self.assertGreater(result["effective_n"], 0)

    def test_small_history_is_provisional_not_available(self) -> None:
        rows = [
            {
                "recorded_at": "2026-08-06T00:00:00Z",
                "task": TASK,
                "execution": {
                    "model": "gpt-5.6-luna",
                    "effort": "medium",
                    "model_snapshot": "gpt-5.6-luna",
                    "first_pass_success": True,
                },
            }
            for _ in range(3)
        ]
        result = personal_calibration(
            TASK,
            CANDIDATE,
            rows,
            as_of=datetime(2026, 8, 7, tzinfo=UTC),
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "provisional")


if __name__ == "__main__":
    unittest.main()
