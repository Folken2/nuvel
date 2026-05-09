"""Messaging-app gateways for {{agent_name}}.

Each gateway exposes a small adapter between an external messaging platform
(Slack, Telegram, MS Teams) and the ADK agent that powers this service.

Slack and Telegram are FastAPI APIRouters mounted on the same server as the
agent (`run_adk.py`). MS Teams runs as a separate aiohttp sidecar
(`teams_bridge.py`) for compatibility with the Microsoft 365 Agents SDK.
"""
