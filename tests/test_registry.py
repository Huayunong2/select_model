from __future__ import annotations

import copy
import unittest

from select_model.registry import registry_summary, validate_model_registry, validate_source_registry
from tests.helpers import model_registry, source_registry


class RegistryTests(unittest.TestCase):
    def test_model_alias_must_target_known_model(self) -> None:
        registry = model_registry()
        registry["aliases"]["bad"] = "missing-model"
        with self.assertRaises(ValueError):
            validate_model_registry(registry)

    def test_api_effort_requires_capacity(self) -> None:
        registry = model_registry()
        del registry["models"]["gpt-5.6-luna"]["effort_capacity"]["medium"]
        with self.assertRaises(ValueError):
            validate_model_registry(registry)

    def test_source_relevance_must_be_bounded(self) -> None:
        registry = source_registry()
        registry["sources"]["codexradar"]["task_relevance"]["coding"] = 1.5
        with self.assertRaises(ValueError):
            validate_source_registry(registry)

    def test_source_official_url_must_be_https(self) -> None:
        registry = copy.deepcopy(source_registry())
        registry["sources"]["codexradar"]["official_urls"] = ["http://example.com"]
        with self.assertRaises(ValueError):
            validate_source_registry(registry)

    def test_source_registry_summary_is_content_addressed(self) -> None:
        summary = registry_summary(source_registry())
        self.assertEqual(summary["kind"], "sources")
        self.assertIn("codexradar", summary["sources"])
        self.assertEqual(len(summary["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
