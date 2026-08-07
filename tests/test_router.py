from __future__ import annotations

import copy
import unittest

from select_model.errors import RoutingError
from select_model.router import route
from tests.helpers import MODELS, envelope, model_registry, route_input, source_registry


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.models = model_registry()
        self.sources = source_registry()

    def test_high_risk_unknown_model_fails_closed(self) -> None:
        data = route_input(risk="high")
        data["candidates"] = [{"model": "unregistered-model", "effort": "medium"}]
        data["evidence"] = [
            envelope("unregistered-model", "codexradar", 90),
            envelope("unregistered-model", "artificial_analysis", 90),
        ]
        with self.assertRaises(RoutingError):
            route(data, model_registry=self.models, source_registry=self.sources)

    def test_high_risk_requires_two_sources(self) -> None:
        data = route_input(risk="high", sources=("codexradar",))
        with self.assertRaises(RoutingError):
            route(data, model_registry=self.models, source_registry=self.sources)

    def test_one_source_route_stability_cannot_be_high(self) -> None:
        data = route_input(risk="medium", sources=("codexradar",))
        result = route(data, model_registry=self.models, source_registry=self.sources)
        self.assertNotEqual(result["route_stability"]["label"], "high")
        self.assertLessEqual(result["route_stability"]["score"], 0.45)

    def test_unknown_source_cannot_route(self) -> None:
        data = route_input(risk="medium", sources=("codexradar",))
        for record in data["evidence"]:
            record["source_id"] = "mystery_board"
        with self.assertRaises(RoutingError):
            route(data, model_registry=self.models, source_registry=self.sources)

    def test_raising_risk_does_not_select_a_lower_public_route_index(self) -> None:
        tied = {
            "codexradar": [80, 80, 80],
            "artificial_analysis": [80, 80, 80],
        }
        low_data = route_input(risk="low", values=tied)
        high_data = route_input(risk="high", values=tied)
        low = route(low_data, model_registry=self.models, source_registry=self.sources)
        high = route(high_data, model_registry=self.models, source_registry=self.sources)
        self.assertGreaterEqual(high["selected"]["route_index"], low["selected"]["route_index"])

    def test_required_capability_is_enforced(self) -> None:
        data = route_input(risk="high")
        data["task"]["required_capabilities"] = ["nonexistent_capability"]
        with self.assertRaises(RoutingError):
            route(data, model_registry=self.models, source_registry=self.sources)

    def test_audit_fingerprints_both_registries(self) -> None:
        result = route(
            route_input(),
            model_registry=self.models,
            source_registry=self.sources,
        )
        self.assertEqual(len(result["audit"]["model_registry"]["sha256"]), 64)
        self.assertEqual(len(result["audit"]["source_registry"]["sha256"]), 64)
        self.assertIn("codexradar", result["audit"]["source_registry"]["sources"])


if __name__ == "__main__":
    unittest.main()
