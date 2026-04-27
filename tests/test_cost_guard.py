"""Tests for CostGuardPlugin pricing calculation."""

import unittest

from nuvel.plugins.cost_guard_plugin import calculate_cost, _find_pricing


SAMPLE_PRICING = {
    "moonshotai/kimi-k2.5": {"input": 0.0000005, "output": 0.000002},
    "anthropic/claude-sonnet-4": {"input": 0.000003, "output": 0.000015},
}


class TestCalculateCost(unittest.TestCase):

    def test_exact_match(self):
        cost = calculate_cost("moonshotai/kimi-k2.5", 1000, 500, SAMPLE_PRICING)
        # 1000 * 0.0000005 + 500 * 0.000002 = 0.0005 + 0.001 = 0.0015
        self.assertAlmostEqual(cost, 0.0015, places=6)

    def test_prefix_strip_match(self):
        """OpenRouter prepends provider prefix — should still match."""
        cost = calculate_cost(
            "openrouter/moonshotai/kimi-k2.5", 1000, 500, SAMPLE_PRICING
        )
        self.assertAlmostEqual(cost, 0.0015, places=6)

    def test_unknown_model_returns_none(self):
        cost = calculate_cost("unknown/model", 1000, 500, SAMPLE_PRICING)
        self.assertIsNone(cost)

    def test_zero_tokens(self):
        cost = calculate_cost("moonshotai/kimi-k2.5", 0, 0, SAMPLE_PRICING)
        self.assertEqual(cost, 0.0)

    def test_empty_model(self):
        cost = calculate_cost("", 1000, 500, SAMPLE_PRICING)
        self.assertIsNone(cost)


class TestFindPricing(unittest.TestCase):

    def test_exact_match(self):
        result = _find_pricing("anthropic/claude-sonnet-4", SAMPLE_PRICING)
        self.assertEqual(result, {"input": 0.000003, "output": 0.000015})

    def test_prefix_strip(self):
        result = _find_pricing("openrouter/anthropic/claude-sonnet-4", SAMPLE_PRICING)
        self.assertEqual(result, {"input": 0.000003, "output": 0.000015})

    def test_no_match(self):
        result = _find_pricing("google/gemini-pro", SAMPLE_PRICING)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
