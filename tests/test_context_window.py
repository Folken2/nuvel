"""Tests for ContextWindowPlugin usage calculation."""

import unittest

from nuvel.plugins.context_window_plugin import (
    compute_context_usage,
    _find_window,
)


SAMPLE_WINDOWS = {
    "moonshotai/kimi-k2.5": 256000,
    "anthropic/claude-sonnet-4": 200000,
}


class TestComputeContextUsage(unittest.TestCase):

    def test_exact_match(self):
        snap = compute_context_usage("anthropic/claude-sonnet-4", 20000, SAMPLE_WINDOWS)
        self.assertEqual(snap["max_tokens"], 200000)
        self.assertEqual(snap["used_tokens"], 20000)
        self.assertEqual(snap["remaining_tokens"], 180000)
        self.assertAlmostEqual(snap["used_pct"], 10.0, places=2)
        self.assertAlmostEqual(snap["remaining_pct"], 90.0, places=2)

    def test_prefix_strip_match(self):
        """OpenRouter prepends a provider prefix — should still match."""
        snap = compute_context_usage(
            "openrouter/moonshotai/kimi-k2.5", 25600, SAMPLE_WINDOWS
        )
        self.assertEqual(snap["max_tokens"], 256000)
        self.assertAlmostEqual(snap["used_pct"], 10.0, places=2)

    def test_unknown_model_omits_percentages(self):
        snap = compute_context_usage("unknown/model", 1000, SAMPLE_WINDOWS)
        self.assertIsNone(snap["max_tokens"])
        self.assertEqual(snap["used_tokens"], 1000)
        self.assertNotIn("used_pct", snap)
        self.assertNotIn("remaining_tokens", snap)

    def test_default_max_fallback(self):
        snap = compute_context_usage(
            "unknown/model", 10000, SAMPLE_WINDOWS, default_max=100000
        )
        self.assertEqual(snap["max_tokens"], 100000)
        self.assertEqual(snap["remaining_tokens"], 90000)
        self.assertAlmostEqual(snap["used_pct"], 10.0, places=2)

    def test_overflow_clamps_remaining_to_zero(self):
        snap = compute_context_usage(
            "anthropic/claude-sonnet-4", 250000, SAMPLE_WINDOWS
        )
        self.assertEqual(snap["remaining_tokens"], 0)
        self.assertGreater(snap["used_pct"], 100.0)

    def test_zero_tokens(self):
        snap = compute_context_usage("anthropic/claude-sonnet-4", 0, SAMPLE_WINDOWS)
        self.assertEqual(snap["used_tokens"], 0)
        self.assertEqual(snap["used_pct"], 0.0)
        self.assertEqual(snap["remaining_tokens"], 200000)


class TestFindWindow(unittest.TestCase):

    def test_exact_match(self):
        self.assertEqual(_find_window("anthropic/claude-sonnet-4", SAMPLE_WINDOWS), 200000)

    def test_prefix_strip(self):
        self.assertEqual(
            _find_window("openrouter/anthropic/claude-sonnet-4", SAMPLE_WINDOWS), 200000
        )

    def test_no_match(self):
        self.assertIsNone(_find_window("google/gemini-pro", SAMPLE_WINDOWS))

    def test_empty_model(self):
        self.assertIsNone(_find_window("", SAMPLE_WINDOWS))


if __name__ == "__main__":
    unittest.main()
