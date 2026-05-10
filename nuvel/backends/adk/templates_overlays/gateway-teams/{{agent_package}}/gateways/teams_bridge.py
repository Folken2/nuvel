"""Local Microsoft 365 / Teams bridge for {{agent_name}}.

Exposes /api/messages (Bot Framework format) and forwards each user
message to the agent server started by run_adk.py. Two operating modes
selected automatically by env: SDK mode when CONNECTIONS__SERVICE_CONNECTION__SETTINGS__*
is set, anonymous mode (Agents Playground) otherwise.

Run with: python -m {{agent_package}}.gateways.teams_bridge
"""

from __future__ import annotations

import os
import asyncio
import logging
import base64
import io
import json

import httpx
from pypdf import PdfReader
from aiohttp import web
from aiohttp.web import Application, Request, Response, run_app
from dotenv import load_dotenv
from microsoft_agents.activity import Activity, ActivityTypes, load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter, start_agent_process
from microsoft_agents.hosting.core import (
    AgentApplication,
    ApplicationOptions,
    Authorization,
    MemoryStorage,
    TurnContext,
    TurnState,
)

from {{agent_package}}.gateways.commands import CommandContext, try_dispatch

logger = logging.getLogger(__name__)


def _first_env(*keys: str, default: str = "") -> str:
    """Return the value of the first non-empty env var in *keys*, or *default*."""
    for k in keys:
        v = os.getenv(k)
        if v is not None and v != "":
            return v
    return default


class AgentBridge(AgentApplication):
    def __init__(self) -> None:
        cfg = load_configuration_from_env(os.environ)
        connection_manager = MsalConnectionManager(**cfg)
        storage = MemoryStorage()
        super().__init__(
            options=ApplicationOptions(
                storage=storage,
                adapter=CloudAdapter(connection_manager=connection_manager),
            ),
            connection_manager=connection_manager,
            authorization=Authorization(storage, connection_manager, **cfg),
            **cfg,
        )

        self.base_url = os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.app_name = os.getenv("AGENT_APP_NAME", "{{agent_name}}")
        self.api_key = os.getenv("API_KEY", "")
        self.timeout_s = float(os.getenv("AGENT_TIMEOUT_SECONDS", "120"))
        self._session_by_conversation: dict[str, str] = {}
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        @self.activity("message", rank=1)
        async def message_handler(context: TurnContext, _: TurnState) -> None:
            prompt = (context.activity.text or "").strip()
            if not prompt:
                await context.send_activity("Please send a message and I will forward it to the agent.")
                return

            # Slash-command interception. Runs before forwarding to the agent.
            conversation_id = (getattr(context.activity.conversation, "id", None) or "default-conversation").replace(" ", "_")
            user_id_for_cmd = getattr(context.activity.from_property, "id", None) or "m365-anonymous"
            cmd_ctx = CommandContext(
                user_id=user_id_for_cmd, channel=conversation_id,
                session_id=f"m365-{conversation_id}", text=prompt,
                reply=lambda t: context.send_activity(t),
            )
            cmd_result = await try_dispatch(prompt, cmd_ctx)
            if cmd_result.handled:
                for line in cmd_result.replies:
                    await context.send_activity(line)
                return

            await context.send_activity("Working on it...")
            response_text = await self._call_agent(context, prompt)
            await context.send_activity(Activity(type=ActivityTypes.message, text=response_text))

    async def _call_agent(self, context: TurnContext, prompt: str) -> str:
        conversation_id = (getattr(context.activity.conversation, "id", None) or "default-conversation").replace(" ", "_")
        user_id = getattr(context.activity.from_property, "id", None) or "m365-anonymous"
        session_id = self._session_by_conversation.get(conversation_id) or f"m365-{conversation_id}"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            create_payload = {"session_id": session_id, "state": {}}
            create_url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions"
            create_response = await client.post(create_url, json=create_payload, headers=headers)
            if create_response.status_code not in (200, 201, 409):
                create_response.raise_for_status()

            run_payload = {
                "app_name": self.app_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {"role": "user", "parts": [{"text": prompt}]},
            }
            run_response = await client.post(f"{self.base_url}/run", json=run_payload, headers=headers)
            run_response.raise_for_status()
            events = run_response.json()

        self._session_by_conversation[conversation_id] = session_id
        if not isinstance(events, list):
            return "No response payload returned by agent."

        texts: list[str] = []
        for event in events:
            if not isinstance(event, dict) or event.get("author") == "user":
                continue
            parts = ((event.get("content") or {}).get("parts") or [])
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(part["text"])

        return texts[-1] if texts else "Agent did not return text."


class AgentClient:
    """Direct HTTP client for proxying messages to run_adk.py."""

    def __init__(self) -> None:
        self.base_url = os.getenv("AGENT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.app_name = os.getenv("AGENT_APP_NAME", "{{agent_name}}")
        self.api_key = os.getenv("API_KEY", "")
        self.timeout_s = float(os.getenv("AGENT_TIMEOUT_SECONDS", "120"))
        self._session_by_conversation: dict[str, str] = {}
        # Preferred explicit flag for intermediate progress messages.
        # Backward compatible with TEAMS_PROGRESS_EVENTS.
        enable_progress_raw = os.getenv("TEAMS_ENABLE_INTERMEDIATE_MESSAGES")
        if enable_progress_raw is None:
            enable_progress_raw = os.getenv("TEAMS_PROGRESS_EVENTS", "true")
        self.enable_progress = enable_progress_raw.lower() in ("true", "1", "yes")
        self.progress_min_delay_ms = int(os.getenv("TEAMS_PROGRESS_MIN_DELAY_MS", "350"))
        self.progress_texts = (
            os.getenv(
                "TEAMS_PROGRESS_TEXTS",
                "Analyzing request...|Inspecting available data...|Running tools...|Preparing final response...",
            )
            .strip()
            .split("|")
        )
        self.progress_texts = [t.strip() for t in self.progress_texts if t.strip()]
        self.enable_attachment_context = os.getenv("TEAMS_ENABLE_ATTACHMENT_CONTEXT", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        self.max_attachment_count = int(_first_env("TEAMS_MAX_ATTACHMENT_COUNT", "GATEWAY_MAX_ATTACHMENT_COUNT", default="5"))
        self.enable_attachment_download = os.getenv("TEAMS_ENABLE_ATTACHMENT_DOWNLOAD", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        self.max_attachment_bytes = int(_first_env("TEAMS_MAX_ATTACHMENT_BYTES", "GATEWAY_MAX_ATTACHMENT_BYTES", default="500000"))
        self.max_inline_b64_chars = int(os.getenv("TEAMS_MAX_INLINE_B64_CHARS", "1500000"))
        self.forward_raw_attachments = os.getenv("TEAMS_FORWARD_RAW_ATTACHMENTS", "false").lower() in (
            "true",
            "1",
            "yes",
        )

    async def ask(self, conversation_id: str, user_id: str, prompt: str, extra_parts: list[dict] | None = None) -> dict:
        conversation_id = (conversation_id or "default-conversation").replace(" ", "_")
        user_id = user_id or "m365-anonymous"
        session_id = self._session_by_conversation.get(conversation_id) or f"m365-{conversation_id}"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            create_payload = {"session_id": session_id, "state": {}}
            create_url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions"
            create_response = await client.post(create_url, json=create_payload, headers=headers)
            if create_response.status_code not in (200, 201, 409):
                create_response.raise_for_status()

            parts = [{"text": prompt}]
            if extra_parts:
                parts.extend(extra_parts)

            run_payload = {
                "app_name": self.app_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {"role": "user", "parts": parts},
            }
            run_response = await client.post(f"{self.base_url}/run", json=run_payload, headers=headers)
            run_response.raise_for_status()
            events = run_response.json()

        self._session_by_conversation[conversation_id] = session_id
        if not isinstance(events, list):
            return {"text": "No response payload returned by agent.", "attachments": []}

        texts: list[str] = []
        attachments: list[dict] = []
        for event in events:
            if not isinstance(event, dict) or event.get("author") == "user":
                continue
            parts = ((event.get("content") or {}).get("parts") or [])
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("text"):
                    texts.append(part["text"])
                inline = part.get("inline_data") or part.get("inlineData")
                if isinstance(inline, dict):
                    mime_type = str(inline.get("mime_type") or inline.get("mimeType") or "application/octet-stream")
                    data_b64 = str(inline.get("data") or "")
                    if data_b64 and len(data_b64) <= self.max_inline_b64_chars:
                        attachments.append(
                            {
                                "contentType": mime_type,
                                "contentUrl": f"data:{mime_type};base64,{data_b64}",
                                "name": "adk-inline-output",
                            }
                        )
                file_data = part.get("file_data") or part.get("fileData")
                if isinstance(file_data, dict):
                    uri = file_data.get("file_uri") or file_data.get("fileUri")
                    mime_type = str(file_data.get("mime_type") or file_data.get("mimeType") or "application/octet-stream")
                    if uri:
                        attachments.append(
                            {
                                "contentType": mime_type,
                                "contentUrl": str(uri),
                                "name": str(file_data.get("display_name") or file_data.get("displayName") or "adk-file"),
                            }
                        )
        return {"text": (texts[-1] if texts else "Agent did not return text."), "attachments": attachments}

    async def ask_with_progress(
        self,
        conversation_id: str,
        user_id: str,
        prompt: str,
        extra_parts: list[dict] | None = None,
    ) -> tuple[list[str], dict]:
        if not self.enable_progress:
            final_result = await self.ask(
                conversation_id=conversation_id, user_id=user_id, prompt=prompt, extra_parts=extra_parts
            )
            return [], final_result

        task = asyncio.create_task(
            self.ask(conversation_id=conversation_id, user_id=user_id, prompt=prompt, extra_parts=extra_parts)
        )
        emitted: list[str] = []
        delay_s = max(0.0, self.progress_min_delay_ms / 1000)

        for text in self.progress_texts:
            if task.done():
                break
            await asyncio.sleep(delay_s)
            if task.done():
                break
            emitted.append(text)

        final_result = await task
        return emitted, final_result


def _has_service_connection_config() -> bool:
    return bool(
        os.getenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID")
        and os.getenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET")
        and os.getenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID")
    )


# TODO(voice-transcription): wire `gateways.transcription.transcribe_audio`
# into Teams once the sidecar gains full inbound-attachment plumbing. Today
# Teams only surfaces inline metadata + best-effort text extraction, so voice
# memos cannot be downloaded and transcribed reliably from this bridge.
async def _extract_attachment_context(payload: dict, client: AgentClient) -> tuple[str, list[str], bool]:
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        return "", [], False

    safe_lines: list[str] = []
    names: list[str] = []
    has_unparsed = False
    for i, item in enumerate(attachments[: client.max_attachment_count], start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"attachment-{i}")
        content_type = str(item.get("contentType") or "unknown")
        content_url = str(item.get("contentUrl") or "")
        size_hint = item.get("size")
        names.append(name)
        safe_lines.append(
            f"{i}. name={name}; content_type={content_type}; has_content_url={'yes' if content_url else 'no'}; size={size_hint if size_hint is not None else 'unknown'}"
        )
        extracted = ""
        if client.enable_attachment_download and content_url:
            extracted = await _try_download_attachment_text(
                content_url=content_url,
                content_type=content_type,
                max_bytes=client.max_attachment_bytes,
            )
        elif item.get("content"):
            extracted = str(item.get("content"))
        if extracted:
            snippet = extracted[:2000]
            safe_lines.append(f"   extracted_text_snippet={snippet!r}")
        else:
            has_unparsed = True

    if not safe_lines:
        return "", [], False

    context_text = (
        "\n\n[Attachment metadata from user message]\n"
        "The user included file attachments. Metadata and extracted snippets follow:\n"
        + "\n".join(safe_lines)
        + "\nUse this extracted data when relevant."
    )
    return context_text, names, has_unparsed


async def _try_download_attachment_text(content_url: str, content_type: str, max_bytes: int) -> str:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(content_url)
            resp.raise_for_status()
            raw = resp.content[:max_bytes]

            # PDFs: extract text page by page (best effort).
            if "pdf" in content_type:
                try:
                    reader = PdfReader(io.BytesIO(raw))
                    extracted: list[str] = []
                    for page in reader.pages:
                        text = page.extract_text() or ""
                        if text.strip():
                            extracted.append(text)
                    return "\n\n".join(extracted)
                except Exception as exc:
                    logger.info("PDF extraction skipped/failed: %s", exc)
                    return ""

            # Plain text-like payloads.
            if content_type.startswith("text/") or "json" in content_type or "csv" in content_type:
                return raw.decode("utf-8", errors="replace")

            return ""
    except Exception as exc:
        logger.info("Attachment download/parse skipped: %s", exc)
        return ""


def _extract_raw_attachment_parts(payload: dict, max_inline_b64_chars: int, max_count: int) -> list[dict]:
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        return []

    raw_parts: list[dict] = []
    for item in attachments[:max_count]:
        if not isinstance(item, dict):
            continue
        content_type = str(item.get("contentType") or "application/octet-stream")
        content_url = str(item.get("contentUrl") or "")

        # If incoming attachment already carries data URI, forward as inline_data.
        if content_url.startswith("data:") and ";base64," in content_url:
            try:
                prefix, data_b64 = content_url.split(";base64,", 1)
                mime_type = prefix.replace("data:", "", 1) or content_type
                if data_b64 and len(data_b64) <= max_inline_b64_chars:
                    raw_parts.append({"inline_data": {"mime_type": mime_type, "data": data_b64}})
                    continue
            except Exception:
                pass

        # Otherwise forward URL as file_data URI.
        if content_url:
            raw_parts.append(
                {
                    "file_data": {
                        "mime_type": content_type,
                        "file_uri": content_url,
                        "display_name": str(item.get("name") or "attachment"),
                    }
                }
            )
            continue

        # If activity carries inline structured content, preserve as text context.
        content_obj = item.get("content")
        if content_obj is not None:
            raw_parts.append({"text": f"[Attachment content: {json.dumps(content_obj, ensure_ascii=True)[:2000]}]"})

    return raw_parts


def main() -> None:
    load_dotenv()
    use_sdk_mode = _has_service_connection_config()
    agent_client = AgentClient()

    if use_sdk_mode:
        agent_app = AgentBridge()

        async def entry_point(req: Request) -> Response:
            return await start_agent_process(req, agent_app, agent_app.adapter)
    else:
        async def entry_point(req: Request) -> Response:
            payload = await req.json()
            text = (payload.get("text") or "").strip()
            conversation = payload.get("conversation") or {}
            from_user = payload.get("from") or {}
            recipient = payload.get("recipient") or {}

            def _activity(text_value: str, attachments: list[dict] | None = None) -> dict:
                msg = {
                    "type": "message",
                    "text": text_value,
                    "from": recipient,
                    "recipient": from_user,
                    "conversation": conversation,
                    "replyToId": payload.get("id"),
                }
                if attachments:
                    msg["attachments"] = attachments
                return msg

            if not text:
                return web.json_response({"activities": [_activity("Please send a message.")]}, status=200)

            conversation_id = (conversation.get("id") or "default-conversation")
            user_id = (from_user.get("id") or "m365-anonymous")

            # Slash-command interception. Runs before forwarding to the agent.
            cmd_ctx = CommandContext(
                user_id=user_id, channel=conversation_id,
                session_id=f"m365-{conversation_id}", text=text,
            )
            cmd_result = await try_dispatch(text, cmd_ctx)
            if cmd_result.handled:
                acts = [_activity(line) for line in cmd_result.replies]
                if not acts:
                    acts = [_activity("")]
                return web.json_response({"activities": acts}, status=200)
            attachment_suffix = ""
            attachment_names: list[str] = []
            has_unparsed_attachments = False
            raw_attachment_parts: list[dict] = []
            if agent_client.forward_raw_attachments:
                raw_attachment_parts = _extract_raw_attachment_parts(
                    payload,
                    max_inline_b64_chars=agent_client.max_inline_b64_chars,
                    max_count=agent_client.max_attachment_count,
                )
            if agent_client.enable_attachment_context:
                attachment_suffix, attachment_names, has_unparsed_attachments = await _extract_attachment_context(
                    payload, agent_client
                )
                if attachment_names:
                    logger.info("Message includes attachments: %s", ", ".join(attachment_names))

            progress_events, final_result = await agent_client.ask_with_progress(
                conversation_id=conversation_id,
                user_id=user_id,
                prompt=f"{text}{attachment_suffix}",
                extra_parts=raw_attachment_parts,
            )
            response_text = str(final_result.get("text") or "")
            output_attachments = final_result.get("attachments") or []

            if has_unparsed_attachments:
                response_text = (
                    f"{response_text}\n\n"
                    "Note: Some uploaded attachments could not be parsed as text yet "
                    f"({', '.join(attachment_names)}). "
                    "For now, share key excerpts or use text/csv/json files with accessible content URLs."
                )

            # Anonymous local mode for Agents Playground: use expectReplies envelope.
            activities = [_activity(p) for p in progress_events]
            activities.append(_activity(response_text, attachments=output_attachments))
            return web.json_response({"activities": activities}, status=200)

    async def health(_: Request) -> Response:
        return Response(text="OK", status=200)

    port = int(os.getenv("TEAMS_BRIDGE_PORT", os.getenv("PORT", "3978")))
    host = os.getenv("TEAMS_BRIDGE_HOST", "localhost")

    app = Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/api/messages", entry_point)
    run_app(app, host=host, port=port, handle_signals=True)


if __name__ == "__main__":
    main()
