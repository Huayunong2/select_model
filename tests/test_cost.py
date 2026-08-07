from __future__ import annotations

import unittest

from select_model.cost import compute_priced_cost


class CostTests(unittest.TestCase):
    def test_cache_write_multiplier_is_applied(self) -> None:
        result = compute_priced_cost(
            {
                "input_per_million": 5.0,
                "cached_input_per_million": 0.5,
                "cache_write_multiplier": 1.25,
                "output_per_million": 30.0,
            },
            {
                "uncached_input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_write_tokens": 1_000_000,
                "output_tokens": 0,
            },
        )
        self.assertEqual(result["cost"], 6.25)

    def test_missing_cache_write_price_fails_closed(self) -> None:
        result = compute_priced_cost(
            {
                "input_per_million": 5.0,
                "cached_input_per_million": 0.5,
                "output_per_million": 30.0,
            },
            {"cache_write_tokens": 1000, "output_tokens": 0, "uncached_input_tokens": 0},
        )
        self.assertIsNone(result["cost"])
        self.assertIn("cache-write", result["errors"][0])

    def test_negative_usage_is_rejected(self) -> None:
        result = compute_priced_cost(
            {
                "input_per_million": 1.0,
                "cached_input_per_million": 0.1,
                "output_per_million": 6.0,
            },
            {"input_tokens": -1, "output_tokens": -1},
        )
        self.assertIsNone(result["cost"])
        self.assertTrue(any("non-negative" in error for error in result["errors"]))

    def test_cache_write_price_is_not_required_when_no_writes_occur(self) -> None:
        result = compute_priced_cost(
            {
                "input_per_million": 1.0,
                "cached_input_per_million": 0.1,
                "output_per_million": 6.0,
            },
            {"input_tokens": 1000, "output_tokens": 100},
        )
        self.assertIsNotNone(result["cost"])

    def test_triggered_long_context_requires_explicit_multipliers(self) -> None:
        result = compute_priced_cost(
            {
                "input_per_million": 1.0,
                "cached_input_per_million": 0.1,
                "output_per_million": 6.0,
                "long_context_threshold": 100,
            },
            {"input_tokens": 101, "output_tokens": 10},
        )
        self.assertIsNone(result["cost"])
        self.assertTrue(any("long-context" in error for error in result["errors"]))

    def test_long_context_multipliers_are_applied(self) -> None:
        result = compute_priced_cost(
            {
                "input_per_million": 1.0,
                "cached_input_per_million": 0.1,
                "cache_write_multiplier": 1.25,
                "output_per_million": 6.0,
                "long_context_threshold": 272_000,
                "long_context_input_multiplier": 2.0,
                "long_context_output_multiplier": 1.5,
            },
            {"input_tokens": 300_000, "output_tokens": 100_000},
        )
        self.assertTrue(result["long_context"])
        self.assertAlmostEqual(result["cost"], 1.5)


if __name__ == "__main__":
    unittest.main()
