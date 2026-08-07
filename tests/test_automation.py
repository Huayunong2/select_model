from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from select_model.automation import (
    _ValidatedRedirectHandler,
    collect_from_mapping_spec,
    import_evidence,
    latest_evidence,
    load_evidence_store,
    sync_registry,
)
from select_model.utils import validate_public_https_url
from tests.helpers import model_registry, source_registry


class AutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = source_registry()

    def test_mapping_collector_and_deduplicated_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "model": "gpt-5.6-luna",
                                "effort": "medium",
                                "score": 80,
                                "sample_size": 1000,
                                "observed_at": "2026-08-06T00:00:00Z",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            spec = {
                "source_id": "codexradar",
                "location": str(snapshot),
                "records_path": "rows",
                "fields": {
                    "model": "model",
                    "effort": "effort",
                    "value": "score",
                    "sample_size": "sample_size",
                    "observed_at": "observed_at",
                },
                "metric": {"name": "score", "higher_is_better": True, "version": "test"},
                "match": "exact",
                "harness": "test",
                "source_url": "https://example.com/codexradar-snapshot",
                "strict": True,
            }
            collected = collect_from_mapping_spec(spec, self.sources)
            self.assertEqual(len(collected["records"]), 1)
            store = root / "evidence.jsonl"
            first = import_evidence(collected, store, self.sources)
            second = import_evidence(collected, store, self.sources)
            self.assertEqual(first["imported"], 1)
            self.assertEqual(second["duplicates"], 1)
            loaded = load_evidence_store(store)
            self.assertEqual(len(loaded["records"]), 1)
            self.assertEqual(len(latest_evidence(loaded["records"], self.sources)), 1)

    def test_private_ip_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_public_https_url("https://127.0.0.1/data.json")

    def test_redirect_target_is_revalidated(self) -> None:
        handler = _ValidatedRedirectHandler()
        with self.assertRaises(ValueError):
            handler.redirect_request(
                None, None, 302, "Found", {}, "https://127.0.0.1/private"
            )

    def test_registry_sync_can_pin_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models.json"
            payload = json.dumps(model_registry(), ensure_ascii=False).encode()
            source.write_bytes(payload)
            import hashlib

            digest = hashlib.sha256(payload).hexdigest()
            output = root / "copy.json"
            result = sync_registry("models", str(source), output, expected_sha256=digest)
            self.assertEqual(result["sha256"], digest)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
