# Your first agent

This page scaffolds a Google ADK agent, runs it locally in dev mode, and sends it a message. Total time: ~5 minutes.

## 1. Scaffold

```bash
nuvel new my-agent --framework adk --description "A helpful assistant."
```

Output:

```
Agent scaffolded at: ./generated-agents/my-agent
Files created: ~50
Framework: adk
```

Default output directory is `./generated-agents/<name>`. Override with `--output-dir`.

The generated tree:

```
my-agent/
├── my_agent/                # the agent package (snake_cased name)
│   ├── agent.py             # the LlmAgent definition
│   ├── prompt/              # instruction frame
│   ├── tools/               # built-in + bundled tools
│   ├── skills/              # bundled knowledge skills
│   └── ...
├── run_adk.py               # the FastAPI server entrypoint
├── requirements.txt
├── .env.example
├── Dockerfile
├── railway.json
└── README.md                # generated, agent-specific
```

## 2. Configure

```bash
cd ./generated-agents/my-agent
cp .env.example .env
```

Edit `.env`. The minimum to run in dev mode is:

```
DEV_MODE=true
OPENROUTER_API_KEY=sk-or-...
```

Get an OpenRouter key at [openrouter.ai/keys](https://openrouter.ai/keys). One free model on the platform is enough to verify the agent works.

## 3. Install the agent's dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The agent has its own venv, separate from nuvel itself. This is intentional — generated agents are real Python projects you own.

## 4. Run

```bash
python run_adk.py
```

You should see:

```
[ADK] Starting server: PORT=8000, DEV_MODE=True, STREAMING=False
[ADK] DEVELOPMENT mode (in-memory sessions)
[ADK] WARNING: No API_KEY set — endpoints are unauthenticated
[ADK] Endpoints added: /health, /debug-info, /
[ADK] Server ready: http://0.0.0.0:8000
```

## 5. Send it a message

In a second terminal:

```bash
curl -s -X POST http://localhost:8000/apps/my-agent/users/me/sessions \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"s1","state":{}}' >/dev/null

curl -s -X POST http://localhost:8000/run \
  -H 'Content-Type: application/json' \
  -d '{
    "app_name":"my-agent",
    "user_id":"me",
    "session_id":"s1",
    "new_message":{"role":"user","parts":[{"text":"hi, what can you do?"}]}
  }' | jq -r '.[].content.parts[]?.text? // empty'
```

Expected: a short, friendly assistant reply.

## Where to go next

- **Make it a chat bot** — see the messaging-gateway recipes: [Slack](../how-to/add-slack.md), [Telegram](../how-to/add-telegram.md), [MS Teams](../how-to/add-teams.md).
- **Give it ~1000 tools** — [Wire 1000+ tools with Composio](../how-to/add-composio-tools.md).
- **Pick a different backend** — see [Pick a backend](../how-to/pick-a-backend.md) for ADK vs. Claude Agent SDK vs. Anthropic Managed Agents.
- **Deploy it** — [Deploy to Railway](../how-to/deploy-to-railway.md).
