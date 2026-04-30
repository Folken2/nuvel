---
name: nuvel
description: Use when the user wants to build a Google ADK agent — scaffolding a new agent, adding tools/skills/prompts to one, or asking how to structure an ADK project. Triggers on phrases like "create an ADK agent", "build a Google agent", "scaffold an agent", "make an agent that does X", "I need an ADK skeleton", or any task involving generated-agents/, LlmAgent, SkillToolset, or the ADK framework. Also use whenever the user mentions `nuvel`, the `nuvel` CLI, or asks about agent architecture patterns / callbacks / HITL / streaming / ADK prompt engineering — nuvel ships the canonical knowledge skills for those topics. Lean toward triggering — if the task touches Google ADK at all, this skill is in scope.
---

# nuvel — building production-ready ADK agents from inside Claude Code

`nuvel` is a meta-agent CLI that scaffolds a battle-tested Google ADK skeleton and ships a library of ADK knowledge skills (agent patterns, prompt engineering, callbacks/HITL, streaming, tool creation, skill design). When the user wants an agent, your job is to drive `nuvel` to produce the skeleton, then fill in the brain — tools, prompts, domain skills — using the bundled knowledge.

Repo: <https://github.com/Folken2/nuvel>.

## Make sure `nuvel` is callable

Before invoking the CLI, confirm it's available:

```bash
nuvel --help                    # if this prints usage, you're good
```

If `command not found`:

1. The user is probably inside the nuvel repo with the venv unactivated. Try `.venv/bin/nuvel --help` first — that's the most common case.
2. If there's no venv yet, run `pip install -e .` from the repo root (or `python3 -m venv .venv && .venv/bin/pip install -e .` to create one). After this, `.venv/bin/nuvel` works; or activate the venv (`source .venv/bin/activate`) and use plain `nuvel`.
3. If the user hasn't cloned the repo, ask before cloning — nuvel only ships from source today (`git clone https://github.com/Folken2/nuvel.git`).

Throughout the rest of this skill `nuvel` means "whichever invocation works on this machine" — substitute `.venv/bin/nuvel` if the venv isn't activated.

## Two modes — pick the right one

**Mode A — Scaffold-and-edit (default for Claude Code).** You drive everything: run `nuvel new` for the skeleton, then write the tools / prompts / skills yourself, consulting `nuvel skills` for ADK conventions. This is almost always the right choice in Claude Code because *you are the smart part* — you can read files, reason, and iterate, which beats a one-shot LLM scaffold.

**Mode B — Meta-agent autopilot.** Run `nuvel run --dev` to launch the meta-agent server (FastAPI on `:8000`), then talk to it via the ADK web UI or `/run_sse/`. Use this only if the user explicitly asks for the autonomous flow, or wants a demo. Requires `OPENROUTER_API_KEY` in the repo's `.env`.

When in doubt: Mode A.

## Feature flags — `--persona` and `--with-composio`

`nuvel new` ships two optional bundles. Pick them up front; they shape the scaffold meaningfully and aren't easy to retrofit.

**`--persona`** — activates the self-evolving agent pattern: a self-rewriting `SOUL.md` (with `read_soul` / `update_soul` tools), a one-time `AWAKENING.md` bootstrap that the agent deletes via `complete_awakening`, and skill-authoring tools (`author_skill`, `update_skill`) so the agent grows its own SKILL.md repertoire over time. The instruction frame switches to the "act-first" persona text. Use this for **agents meant to live for months and develop a stable character** — personal assistants, long-running companions, agents that should accumulate knowledge across sessions. Do **not** use for stateless task bots, customer-support agents, or anything that should behave consistently across deploys: a support bot that rewrites its own SOUL.md mid-conversation is a regression, not a feature.

**`--with-composio`** — wires the Composio Tool Router via ADK's `McpToolset`. One `composio.create(user_id=...)` call gives the agent ~1000 toolkits (Gmail, GitHub, Slack, Notion, Calendar, etc.) behind a single hosted MCP endpoint. Composio handles auth, tool discovery, and execution. Requires `COMPOSIO_API_KEY` at runtime; without it the toolset gracefully no-ops. Use this when **the agent's value is breadth of integrations** rather than depth in one domain. Independent of `--persona` — combine freely.

**When to combine.** Personal agent meant to act across the user's whole digital life: `--persona --with-composio`. Pure task bot needing many integrations: `--with-composio` only. Domain-specialist that should never drift (e.g. SQL analyst, data-pipeline operator): no flags. Personal companion without external tools: `--persona` only.

**Universal improvements (always on, regardless of flags):**
- `LazySkillToolset` rebuilds on `SKILL.md` mtime change — new skills become queryable on the next agent invocation, no process restart. SOUL.md and memory edits are also picked up immediately (read fresh each turn).
- `config/paths.py` exposes `SOUL_FILE` / `AWAKENING_FILE` / `SKILLS_DIR` / `MEMORY_DIR` env vars with in-repo defaults. Set these to a mounted volume path (e.g. `/data/...` on Railway) for cross-deploy persistence; leave unset locally. `seed_volume_if_empty()` runs at boot and copies in-repo seeds into an empty volume on first deploy.

## The canonical Mode A workflow

Treat each step as a checkpoint — verify the previous step before moving on. Don't batch 5 steps and hope.

### 1. Confirm the spec with the user (always)

Before scaffolding, get clarity on three things — silently if obvious from context, explicitly if not:

- **Name** (kebab-case, ≤40 chars, must start with a letter, no consecutive hyphens — `nuvel new` validates this).
- **One-line description** — used in the README and as a hint for prompt design.
- **What the agent actually does** — list the tools and the trigger phrases. This is what you'll spend most of your time on; the skeleton is free.

If the user gave you a vague brief ("an agent that helps with X"), name 2-3 concrete tools you'd build and ask if that's the shape. Don't scaffold based on guesswork — `nuvel new` is fast but rewriting domain logic later is not.

### 2. Scaffold the skeleton

```bash
nuvel new <kebab-name> \
  --description "one-line description" \
  --output-dir ./generated-agents \
  [--persona] [--with-composio]
```

The default `--output-dir` is `./generated-agents` relative to wherever you run from. See the **Feature flags** section above to decide on `--persona` and `--with-composio`. Pass `--system-prompt` only if the user gave you exact text — otherwise leave it off and write the prompt properly in step 4.

Verify: `ls generated-agents/<name>/` should show `<snake_name>/`, `run_adk.py`, `requirements.txt`, `.env.example`, `tests/`.

### 3. Survey the relevant ADK knowledge

`nuvel` bundles 7 knowledge skills. Don't read them all — pick by topic:

```bash
nuvel skills list                    # see what's available
nuvel skills search <topic>          # narrow by keyword
```

Available skills (all live in `nuvel/skills/<slug>/SKILL.md` inside the nuvel repo):

| Slug | Read when… |
| --- | --- |
| `adk-agent-patterns` | Choosing between LlmAgent / LoopAgent / SequentialAgent / multi-agent |
| `adk-tool-creation` | Writing function tools (signatures, ToolContext, errors) |
| `adk-prompt-engineering` | Designing the system prompt — dynamic instructions, InstructionProvider |
| `adk-callbacks-hitl` | Adding human-in-the-loop gates, before/after callbacks, state |
| `adk-streaming` | Voice / video / Gemini Live API agents |
| `adk-skill-creation` | Authoring SKILL.md files for the agent's own domain knowledge |
| `adk-skill-design-patterns` | Five canonical skill shapes — pick before writing one |

Read the SKILL.md directly with the Read tool — they're tuned for progressive disclosure (short top, deep references).

### 4. Fill in the brain

The scaffold gives you the skeleton; now write the actual agent. In `generated-agents/<name>/<snake_name>/`:

1. **`tools/`** — write one Python file per tool, following `adk-tool-creation`. Use `ToolContext` correctly, return structured dicts, raise specific exceptions.
2. **`prompt/instructions.py`** — system prompt. Apply `adk-prompt-engineering`; prefer `InstructionProvider` for any state-dependent text.
3. **`skills/`** — domain SKILL.md files for knowledge the agent needs at runtime (escalation rules, API quirks, lookup tables). Apply `adk-skill-design-patterns` to pick a shape.
4. **`agent.py`** — wire the tools and SkillToolset together. The scaffold already has the skeleton; you mostly add tool imports and register them.
5. **`tests/`** — mirror existing test files. Add a unit test per tool and an end-to-end smoke test.

### 5. Verify

```bash
cd generated-agents/<name>
python -m pytest tests/ -q       # tests must pass
python -c "from <snake_name>.agent import root_agent; print(root_agent.name)"   # imports cleanly
```

If the user wants to actually run it: `cp .env.example .env`, add their `OPENROUTER_API_KEY`, then `python run_adk.py`. The agent runs at `http://localhost:8000`.

## Suppressing CLI startup noise

`nuvel` prints two lines of dependency warnings (`authlib` deprecation, `SKILL_TOOLSET` experimental flag) to stderr on every invocation. They're harmless. If the noise gets in the way of parsing output, redirect stderr:

```bash
nuvel skills list 2>/dev/null
```

Don't try to fix the warnings — they're upstream and the project tolerates them deliberately.

## Common pitfalls

- **Kebab vs snake confusion.** The agent *name* is kebab-case (`my-agent`), but the *Python package* inside is snake_case (`my_agent`). The scaffolder handles the conversion; just don't pass `my_agent` to `nuvel new`.
- **Running `nuvel run` for scaffolding.** `nuvel run` launches the meta-agent server; it doesn't create files. Use `nuvel new` to create.
- **Skipping the knowledge skills.** The bundled skills exist *because* getting ADK right is non-obvious. Reading `adk-tool-creation` before writing tools saves more time than it costs every single time.
- **Generating a "smart" scaffold via `nuvel new --system-prompt "..."`.** The flag literally drops your text into the template; it doesn't refine or validate. Write the prompt properly in `prompt/instructions.py` after scaffolding instead.
- **Working outside the nuvel repo.** `nuvel new` is happy to scaffold anywhere via `--output-dir`, but the bundled skills (`nuvel skills list`) only resolve when you have nuvel installed. If the user wants the agent in a different repo, scaffold inside nuvel first, then `mv generated-agents/<name>` to its final home.

## Quick reference

```bash
# Scaffold (base)
nuvel new <kebab-name> --description "…" --output-dir ./generated-agents

# Self-evolving personal agent with broad tool access
nuvel new <kebab-name> --description "…" --persona --with-composio

# Just persona (no external integrations)
nuvel new <kebab-name> --description "…" --persona

# Just Composio (stateless task bot with many integrations)
nuvel new <kebab-name> --description "…" --with-composio

# Knowledge
nuvel skills list
nuvel skills search prompt

# Autopilot mode (only when asked)
nuvel run --dev
```

When the user says "build me an X agent", the answer is almost never "let me think about it" — it's "let me scaffold the skeleton and then we'll fill in the tools." Show progress; the skeleton is free.
