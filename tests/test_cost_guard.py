"""Tests for CostGuardPlugin pricing calculation and per-session tracking."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from google.genai import types

from nuvel.plugins.cost_guard_plugin import (
    CostGuardPlugin,
    calculate_cost,
    _find_pricing,
)


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



def _ctx(state, invocation_id="inv-1", agent_name="root"):
    return SimpleNamespace(state=state, invocation_id=invocation_id, agent_name=agent_name)


def _response(prompt_tokens, completion_tokens):
    return SimpleNamespace(
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=completion_tokens,
        )
    )


class TestCostGuardPerSession(unittest.TestCase):
    """One plugin instance serves many sessions on a shared server."""

    def _plugin(self, budget="0"):
        with mock.patch.dict(os.environ, {"COST_GUARD_BUDGET": budget}):
            plugin = CostGuardPlugin()
        plugin._pricing = SAMPLE_PRICING
        return plugin

    def _call(self, plugin, ctx, model, prompt_tokens=1000, completion_tokens=500):
        request = SimpleNamespace(model=model)
        blocked = asyncio.run(
            plugin.before_model_callback(callback_context=ctx, llm_request=request)
        )
        if blocked is None:
            asyncio.run(
                plugin.after_model_callback(
                    callback_context=ctx,
                    llm_response=_response(prompt_tokens, completion_tokens),
                )
            )
        return blocked

    def test_sessions_keep_separate_totals(self):
        plugin = self._plugin()
        a, b = {}, {}
        self._call(plugin, _ctx(a, "inv-a"), "moonshotai/kimi-k2.5")
        self._call(plugin, _ctx(a, "inv-a2"), "moonshotai/kimi-k2.5")
        self._call(plugin, _ctx(b, "inv-b"), "moonshotai/kimi-k2.5")
        self.assertAlmostEqual(a["cost_guard"]["session_cost_usd"], 0.003, places=6)
        self.assertAlmostEqual(b["cost_guard"]["session_cost_usd"], 0.0015, places=6)

    def test_budget_blocks_only_the_session_over_it(self):
        plugin = self._plugin(budget="0.002")
        spent, fresh = {}, {}
        self.assertIsNone(self._call(plugin, _ctx(spent, "inv-1"), "moonshotai/kimi-k2.5"))
        self.assertIsNone(self._call(plugin, _ctx(spent, "inv-2"), "moonshotai/kimi-k2.5"))
        self.assertIsNotNone(self._call(plugin, _ctx(spent, "inv-3"), "moonshotai/kimi-k2.5"))
        self.assertTrue(spent["cost_guard"]["blocked"])
        self.assertIsNone(self._call(plugin, _ctx(fresh, "inv-4"), "moonshotai/kimi-k2.5"))
        self.assertFalse(fresh["cost_guard"]["blocked"])

    def test_interleaved_calls_price_their_own_model(self):
        plugin = self._plugin()
        a, b = {}, {}
        ctx_a, ctx_b = _ctx(a, "inv-a"), _ctx(b, "inv-b")
        asyncio.run(plugin.before_model_callback(
            callback_context=ctx_a, llm_request=SimpleNamespace(model="moonshotai/kimi-k2.5")))
        asyncio.run(plugin.before_model_callback(
            callback_context=ctx_b, llm_request=SimpleNamespace(model="anthropic/claude-sonnet-4")))
        asyncio.run(plugin.after_model_callback(callback_context=ctx_a, llm_response=_response(1000, 500)))
        asyncio.run(plugin.after_model_callback(callback_context=ctx_b, llm_response=_response(1000, 500)))
        self.assertEqual(a["cost_guard"]["model"], "moonshotai/kimi-k2.5")
        self.assertAlmostEqual(a["cost_guard"]["call_cost_usd"], 0.0015, places=6)
        self.assertEqual(b["cost_guard"]["model"], "anthropic/claude-sonnet-4")
        # 1000 * 0.000003 + 500 * 0.000015 = 0.0105
        self.assertAlmostEqual(b["cost_guard"]["call_cost_usd"], 0.0105, places=6)
        self.assertEqual(plugin._models, {})

    def test_after_run_drops_calls_that_never_finished(self):
        plugin = self._plugin()
        asyncio.run(plugin.before_model_callback(
            callback_context=_ctx({}, "inv-x"), llm_request=SimpleNamespace(model="m")))
        asyncio.run(plugin.after_run_callback(
            invocation_context=SimpleNamespace(invocation_id="inv-x")))
        self.assertEqual(plugin._models, {})


if __name__ == "__main__":
    unittest.main()
