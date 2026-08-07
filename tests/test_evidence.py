from __future__ import annotations

import unittest
from datetime import UTC, datetime

from select_model.evidence import build_evidence_profiles
from tests.helpers import MODELS, envelope, source_registry


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = source_registry()
        self.as_of = datetime(2026, 8, 7, tzinfo=UTC)
        self.candidates = [{"model": model, "effort": "medium"} for model in MODELS[:2]]

    def test_missing_metadata_never_increases_weight(self) -> None:
        complete = build_evidence_profiles(
            self.candidates,
            [
                envelope(MODELS[0], "codexradar", 90),
                envelope(MODELS[1], "codexradar", 80),
            ],
            self.registry,
            "coding",
            as_of=self.as_of,
            strict=False,
        )
        incomplete = build_evidence_profiles(
            self.candidates,
            [
                envelope(MODELS[0], "codexradar", 90, complete=False),
                envelope(MODELS[1], "codexradar", 80, complete=False),
            ],
            self.registry,
            "coding",
            as_of=self.as_of,
            strict=False,
        )
        complete_weight = complete["profiles"][f"{MODELS[0]}:medium"]["panel_signals"][0]["effective_weight"]
        incomplete_weight = incomplete["profiles"][f"{MODELS[0]}:medium"]["panel_signals"][0]["effective_weight"]
        self.assertLess(incomplete_weight, complete_weight)

    def test_unknown_source_is_rejected(self) -> None:
        record = envelope(MODELS[0], "codexradar", 90)
        record["source_id"] = "mystery_board"
        result = build_evidence_profiles(
            [self.candidates[0]],
            [record],
            self.registry,
            "coding",
            as_of=self.as_of,
        )
        self.assertEqual(result["accepted_records"], 0)
        self.assertIsNone(result["profiles"][f"{MODELS[0]}:medium"])
        self.assertIn("unknown source_id", result["rejected"][0]["errors"][0])

    def test_stale_evidence_is_rejected(self) -> None:
        record = envelope(MODELS[0], "codexradar", 90, observed_at="2026-01-01T00:00:00Z")
        result = build_evidence_profiles(
            [self.candidates[0]],
            [record],
            self.registry,
            "coding",
            as_of=self.as_of,
        )
        self.assertEqual(result["accepted_records"], 0)
        self.assertIn("stale", result["rejected"][0]["errors"][0])

    def test_strict_rejects_legacy_exact_evidence(self) -> None:
        candidate = {
            "model": MODELS[0],
            "effort": "medium",
            "evidence": {"codexradar": {"score": 90, "match": "exact", "age_hours": 1}},
        }
        result = build_evidence_profiles(
            [candidate],
            None,
            self.registry,
            "coding",
            as_of=self.as_of,
            strict=True,
        )
        self.assertEqual(result["accepted_records"], 0)

    def test_exact_snapshot_mismatch_is_rejected(self) -> None:
        candidate = {
            "model": MODELS[0],
            "effort": "medium",
            "snapshot": "current-snapshot",
        }
        record = envelope(MODELS[0], "codexradar", 90)
        record["subject"]["snapshot"] = "old-snapshot"
        result = build_evidence_profiles(
            [candidate],
            [record],
            self.registry,
            "coding",
            as_of=self.as_of,
            strict=True,
        )
        self.assertEqual(result["accepted_records"], 0)
        self.assertIn("snapshot", result["rejected"][0]["errors"][0])

    def test_source_url_with_embedded_credentials_is_rejected(self) -> None:
        record = envelope(MODELS[0], "codexradar", 90)
        record["source_url"] = "https://user:secret@example.com/source"
        result = build_evidence_profiles(
            [self.candidates[0]],
            [record],
            self.registry,
            "coding",
            as_of=self.as_of,
        )
        self.assertEqual(result["accepted_records"], 0)
        self.assertIn("credentials", result["rejected"][0]["errors"][0])

    def test_one_source_cannot_have_high_confidence(self) -> None:
        result = build_evidence_profiles(
            self.candidates,
            [
                envelope(MODELS[0], "codexradar", 90),
                envelope(MODELS[1], "codexradar", 80),
            ],
            self.registry,
            "coding",
            as_of=self.as_of,
        )
        profile = result["profiles"][f"{MODELS[0]}:medium"]
        self.assertEqual(profile["source_count"], 1)
        self.assertEqual(profile["confidence"], "low")
        self.assertLessEqual(profile["confidence_score"], 0.45)

    def test_aggregation_uses_source_internal_ranks(self) -> None:
        records = [
            envelope(MODELS[0], "codexradar", 0.9),
            envelope(MODELS[1], "codexradar", 0.8),
            envelope(MODELS[0], "artificial_analysis", 900),
            envelope(MODELS[1], "artificial_analysis", 800),
        ]
        result = build_evidence_profiles(
            self.candidates,
            records,
            self.registry,
            "coding",
            as_of=self.as_of,
        )
        top = result["profiles"][f"{MODELS[0]}:medium"]
        bottom = result["profiles"][f"{MODELS[1]}:medium"]
        self.assertEqual(top["routing_index"], 100.0)
        self.assertEqual(bottom["routing_index"], 0.0)


if __name__ == "__main__":
    unittest.main()
