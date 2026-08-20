"""Join a Buzz relay and talk in the group, as {{agent_name}}.

Buzz groups are NIP-29 relay-based groups on Nostr: a chat message is a
kind-9 event carrying an ``h`` tag with the group id, and the relay is a
plain WebSocket endpoint speaking NIP-01 frames (``REQ`` / ``EVENT`` /
``OK`` / ``EOSE`` / ``NOTICE``).

This worker is the fourth entrypoint into the same agent — it subscribes to
the group, decides which messages are addressed to it, runs each one through
the shared :class:`~{{agent_package}}.acp.runtime.AgentRuntime`, and publishes
the answer back as a signed kind-9 event.

    python -m {{agent_package}}.buzz_relay

Configuration (see ``.env.example``): ``BUZZ_RELAY_URL``, ``BUZZ_GROUP_ID``,
``BUZZ_REPLY_POLICY``, ``BUZZ_AGENT_HANDLE``, plus the Nostr key resolution in
``nostr_identity.load_identity``.

Unlike the ACP adapter, chat is not a streaming medium: updates are collected
for a turn and published as one message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass

from .acp.runtime import AgentRuntime
from .buzz_persona import BuzzPersona
from .nostr_identity import NostrIdentity, load_identity

logger = logging.getLogger(__name__)

# NIP-01 / NIP-29 event kinds.
KIND_METADATA = 0
KIND_GROUP_CHAT = 9

# Relay reconnect backoff, in seconds.
_BACKOFF_START = 1.0
_BACKOFF_MAX = 60.0

# Nostr messages are chat-sized; refuse to publish a wall of text.
MAX_REPLY_CHARS = 4000


@dataclass
class RelayConfig:
    """Everything the relay worker reads from the environment."""

    relay_url: str = ""
    group_id: str = ""
    reply_policy: str = "mention"  # "mention" | "all"
    handle: str = ""
    publish_profile: bool = True

    @classmethod
    def from_env(cls) -> "RelayConfig":
        from .agent import AGENT_NAME

        return cls(
            relay_url=(os.getenv("BUZZ_RELAY_URL") or "").strip(),
            group_id=(os.getenv("BUZZ_GROUP_ID") or "").strip(),
            reply_policy=(os.getenv("BUZZ_REPLY_POLICY") or "mention").strip().lower(),
            handle=(os.getenv("BUZZ_AGENT_HANDLE") or AGENT_NAME).strip(),
            publish_profile=os.getenv("BUZZ_PUBLISH_PROFILE", "true").lower()
            not in ("false", "0", "no"),
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.relay_url:
            problems.append("No relay. Set BUZZ_RELAY_URL to the relay's WebSocket URL.")
        elif not self.relay_url.startswith(("ws://", "wss://")):
            problems.append(f"BUZZ_RELAY_URL must be ws:// or wss:// (got {self.relay_url!r}).")
        if not self.group_id:
            problems.append("No group. Set BUZZ_GROUP_ID to the NIP-29 group id.")
        if self.reply_policy not in ("mention", "all"):
            problems.append(
                f"BUZZ_REPLY_POLICY must be 'mention' or 'all' (got {self.reply_policy!r})."
            )
        return problems


def _tag_values(event: dict, name: str) -> list[str]:
    return [t[1] for t in event.get("tags", []) if len(t) >= 2 and t[0] == name]


class BuzzRelay:
    """Subscribes to a Buzz group, answers what's addressed to the agent."""

    def __init__(
        self,
        config: RelayConfig | None = None,
        identity: NostrIdentity | None = None,
        runtime: AgentRuntime | None = None,
    ) -> None:
        self.config = config or RelayConfig.from_env()
        self.identity = identity or load_identity()
        self.runtime = runtime or AgentRuntime()
        self.persona = BuzzPersona.build()
        # The whole group shares one session, so the agent follows the thread.
        self.session_id = f"buzz-{self.config.group_id}"
        self._seen: set[str] = set()

    # ── addressing ───────────────────────────────────────────────────

    def is_addressed(self, event: dict) -> bool:
        """Should the agent answer this message?

        Under the default ``mention`` policy: a ``p`` tag naming the agent, its
        npub in the body, or an ``@handle`` mention. Under ``all``: anything in
        the group that isn't the agent's own message.
        """
        if event.get("pubkey") == self.identity.pubkey_hex:
            return False  # never answer ourselves
        if self.config.reply_policy == "all":
            return True

        if self.identity.pubkey_hex in _tag_values(event, "p"):
            return True
        content = (event.get("content") or "").lower()
        return self.identity.npub.lower() in content or (
            bool(self.config.handle) and f"@{self.config.handle.lower()}" in content
        )

    # ── turn handling ────────────────────────────────────────────────

    async def answer(self, event: dict) -> str:
        """Run one group message through the agent; return the reply text."""
        author = event.get("pubkey", "")
        prompt = (
            f"[buzz group {self.config.group_id}] "
            f"{author[:8] or 'someone'} says: {event.get('content', '')}"
        )

        chunks: list[str] = []
        await self.runtime.ensure_session(author or "buzz", self.session_id)
        async for update in self.runtime.run_turn(author or "buzz", self.session_id, prompt):
            if update.kind == "text":
                chunks.append(update.text)
            elif update.kind == "tool_call":
                logger.info("tool %s(%s)", update.tool_name, update.tool_args)

        reply = "".join(chunks).strip()
        if len(reply) > MAX_REPLY_CHARS:
            reply = reply[: MAX_REPLY_CHARS - 1].rstrip() + "…"
        return reply

    def build_reply(self, event: dict, text: str) -> dict:
        """A signed kind-9 reply, threaded onto the message it answers."""
        tags = [
            ["h", self.config.group_id],
            ["e", event.get("id", ""), "", "reply"],
            ["p", event.get("pubkey", "")],
        ]
        return self.identity.sign_event(KIND_GROUP_CHAT, text, tags)

    def profile_event(self) -> dict:
        """The kind-0 metadata event announcing who this agent is."""
        return self.identity.sign_event(
            KIND_METADATA, self.persona.to_profile_content(), []
        )

    # ── relay protocol ───────────────────────────────────────────────

    def subscription_request(self, sub_id: str, since: int | None = None) -> list:
        """The NIP-01 ``REQ`` frame subscribing to this group's chat."""
        filters: dict = {"kinds": [KIND_GROUP_CHAT], "#h": [self.config.group_id]}
        if since is not None:
            filters["since"] = since
        return ["REQ", sub_id, filters]

    async def _handle_frame(self, websocket, frame: list) -> None:
        kind = frame[0] if frame else ""
        if kind == "NOTICE":
            logger.warning("relay notice: %s", frame[1:])
            return
        if kind == "OK" and len(frame) >= 3 and not frame[2]:
            logger.warning("relay rejected %s: %s", frame[1], frame[3:] or "")
            return
        if kind != "EVENT" or len(frame) < 3:
            return

        event = frame[2]
        event_id = event.get("id", "")
        if event_id in self._seen:
            return
        self._seen.add(event_id)

        if not NostrIdentity.verify_event(event):
            logger.warning("dropping event %s: bad id or signature", event_id[:8])
            return
        if not self.is_addressed(event):
            return

        try:
            reply = await self.answer(event)
        except Exception:  # noqa: BLE001 — one bad turn must not end the worker
            logger.exception("Turn failed for event %s", event_id[:8])
            return
        if not reply:
            return
        await websocket.send(json.dumps(["EVENT", self.build_reply(event, reply)]))

    async def _session(self) -> None:
        """One connection: subscribe, then service frames until it drops."""
        import websockets

        async with websockets.connect(self.config.relay_url) as websocket:
            logger.info(
                "connected to %s as %s", self.config.relay_url, self.identity.npub
            )
            if self.config.publish_profile:
                await websocket.send(json.dumps(["EVENT", self.profile_event()]))

            sub_id = uuid.uuid4().hex[:16]
            await websocket.send(
                json.dumps(self.subscription_request(sub_id, since=int(time.time())))
            )

            async for raw in websocket:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, list):
                    await self._handle_frame(websocket, frame)

    async def run(self) -> None:
        """Stay connected, reconnecting with exponential backoff."""
        backoff = _BACKOFF_START
        try:
            while True:
                try:
                    await self._session()
                    backoff = _BACKOFF_START  # a clean close: retry promptly
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — relays drop; reconnect
                    logger.warning("relay connection lost (%s); retrying in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
        finally:
            await self.runtime.aclose()


async def _amain() -> int:
    config = RelayConfig.from_env()
    problems = config.validate()
    relay = None
    if not problems:
        relay = BuzzRelay(config)
        problems = relay.runtime.agent.config.validate()
    if problems:
        for problem in problems:
            logger.error("config: %s", problem)
        return 1

    logger.info("joining group %s as %s", config.group_id, relay.identity.npub)
    await relay.run()
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.getLevelName(os.getenv("LOG_LEVEL", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        raise SystemExit(asyncio.run(_amain()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
