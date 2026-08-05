"""Minimal agent for wiring tests — never actually invoked."""

from __future__ import annotations

from google.adk.agents import Agent

root_agent = Agent(
    name="test_agent",
    model="gemini-2.0-flash-exp",
    description="Minimal agent for wiring tests; never invoked.",
    instruction="You are a test agent.",
)
