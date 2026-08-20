# Reach an agent on MS Teams

`--with-teams` adds a Microsoft Teams gateway as a **standalone aiohttp sidecar process**, separate from the FastAPI agent server. It uses the [Microsoft 365 Agents SDK](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/) for inbound JWT validation and outbound activity posting.

ADK only.

!!! warning "Teams runs as a second process"
    Slack and Telegram mount on the existing agent server. Teams runs as `python -m {agent_package}.gateways.teams_bridge` — a separate process listening on its own port. You'll need to run **two** processes when Teams is enabled.

    The reason: the M365 Agents SDK is aiohttp-based and doesn't compose cleanly inside FastAPI. The bridge proxies to the agent server's REST API.

## Scaffold

```bash
nuvel agent create my-agent --framework adk --with-teams
```

The overlay drops `my_agent/gateways/teams_bridge.py` and adds these to `requirements.txt`:

- `microsoft-agents-hosting-aiohttp`
- `microsoft-agents-authentication-msal`
- `aiohttp`
- `pypdf` (used for ingesting PDF attachments shared with the bot)

## Two operating modes

The bridge auto-selects between two modes based on env config:

- **SDK mode (production):** full Bot Framework JWT validation. Triggered when `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__{CLIENTID,CLIENTSECRET,TENANTID}` are set.
- **Anonymous mode (local dev):** plain JSON in/out using the Bot Framework `expectReplies` envelope. Triggered when the SDK config is absent. **Do not expose anonymous mode to the public internet** — it accepts unauthenticated POSTs.

## Local development with Agents Playground

For local testing without registering a bot in Azure:

1. Install the [Microsoft 365 Agents Playground](https://aka.ms/agents-playground).
2. Configure `.env` for anonymous mode (don't set the `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__*` vars).
3. In two terminals:

   ```bash
   # Terminal 1: the agent server
   python run_adk.py

   # Terminal 2: the Teams sidecar
   python -m my_agent.gateways.teams_bridge
   ```

4. In Agents Playground, set the messaging endpoint to `http://localhost:3978/api/messages`. Send a message; the agent replies.

## Production with Azure Bot Service

1. **Register the bot** in [Azure Bot Service](https://portal.azure.com/#create/Microsoft.AzureBot) or via the [Teams Developer Portal](https://dev.teams.microsoft.com/).
2. Note the bot's **App ID**, **App Password / Client Secret**, and **Tenant ID**.
3. Set in `.env`:

   ```
   CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=<App ID>
   CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=<App Password>
   CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=<Tenant ID>

   # Bridge → agent server (defaults usually fine):
   AGENT_BASE_URL=http://127.0.0.1:8000
   AGENT_APP_NAME=my-agent
   API_KEY=<same as the agent server's API_KEY>

   # Bridge runtime:
   TEAMS_BRIDGE_PORT=3978
   ```

4. Set the bot's **messaging endpoint** in Azure to `https://<bridge-host>:3978/api/messages`.
5. Run the agent server and the Teams sidecar (separate processes — typically separate containers in production).

## All available env vars

```
# Required in production (SDK mode):
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=

# Bridge → agent server:
AGENT_BASE_URL=http://127.0.0.1:8000
AGENT_APP_NAME=<defaults to scaffolded agent name>
API_KEY=
AGENT_TIMEOUT_SECONDS=120

# Bridge runtime:
TEAMS_BRIDGE_PORT=3978
TEAMS_BRIDGE_HOST=localhost

# Progress / streaming UX (sends interim "Working on it..." messages):
TEAMS_ENABLE_INTERMEDIATE_MESSAGES=true
TEAMS_PROGRESS_TEXTS=Analyzing request...|Inspecting available data...|Running tools...|Preparing final response...
TEAMS_PROGRESS_MIN_DELAY_MS=350

# Attachment ingestion (PDFs, text files, images):
TEAMS_ENABLE_ATTACHMENT_CONTEXT=true
TEAMS_MAX_ATTACHMENT_COUNT=5
TEAMS_ENABLE_ATTACHMENT_DOWNLOAD=true
TEAMS_MAX_ATTACHMENT_BYTES=500000
TEAMS_MAX_INLINE_B64_CHARS=1500000
TEAMS_FORWARD_RAW_ATTACHMENTS=false
```

## Session model

`session_id = f"m365-{conversation.id}"` per the Bot Framework `Conversation` resource. One Teams 1:1 chat = one session; one Teams channel post + thread = one session per thread.

## Troubleshooting

- **Bridge prints "ANONYMOUS mode" but you set the SDK env vars** → check spelling. The detection is exact match on `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID`, `..._CLIENTSECRET`, `..._TENANTID` — all uppercase, double underscores.
- **Bridge can't reach agent server** → check `AGENT_BASE_URL` is right and the agent server is running. Test with `curl $AGENT_BASE_URL/health`.
- **JWT validation errors** → token mismatch between Azure registration and `.env` values, or clock skew on the bridge host.

## Production deployment

Most operators deploy the agent server and the Teams bridge as **two separate containers** in the same VPC. The bridge listens on `:3978` (Bot Framework convention); the agent server on `:8000`. Only the bridge needs a public hostname (the bot's messaging endpoint). The agent server stays internal.
