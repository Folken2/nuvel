---
name: managed-agents-skills-memory
description: Skills and memory stores in Anthropic Managed Agents — Anthropic prebuilt skills (xlsx/docx/pptx/pdf), custom skills via the Skills API, and memory stores for cross-session persistence with versioning and audit. Read when adding domain expertise the agent should auto-load, when the agent needs persistent state across sessions, or when distinguishing memory from session resources.
type: knowledge
---

# Skills and memory in Managed Agents

Two ways to give the agent context beyond its system prompt: **skills** (knowledge it loads on demand) and **memory stores** (state that persists across sessions).

## Skills — domain expertise on demand

A skill is a folder with a `SKILL.md` file. The skill's description sits in context by default; the agent reads the full file when the task calls for it. Two types — both work the same way at runtime:

| Type | What it is | Reference by |
|---|---|---|
| **Anthropic prebuilt** | Common document tasks shipped by Anthropic | Name: `xlsx`, `docx`, `pptx`, `pdf` |
| **Custom** | Skills you've authored, stored in your org via the Skills API | `skill_id` + optional `version` |

Attach to the agent (max 20):

```yaml
skills:
  - type: anthropic
    skill_id: xlsx
  - type: anthropic
    skill_id: pdf
  - type: custom
    skill_id: skill_abc123
    version: latest    # or a specific version number
```

Updates live — re-run `setup.py` after changing the `skills` list to push a new agent version.

### Authoring a custom skill

The Skills API is its own endpoint group (`/v1/skills`). Two-level versioning: skills have many versions; sessions reference one version (or `latest`). Beta header `skills-2025-10-02` (set automatically by the SDK on `client.beta.skills.*` calls).

Minimal flow:

```python
# 1. Create the skill record (one-time)
skill = client.beta.skills.create(name="financial-modeling", description="DCF + sensitivity analysis patterns")

# 2. Upload a version with content
version = client.beta.skills.versions.create(
    skill_id=skill.id,
    skill_md=open("skills/financial-modeling/SKILL.md").read(),
    # ... reference files via additional fields ...
)

# 3. Reference in agent.yaml
# skills:
#   - {type: custom, skill_id: skill.id, version: "latest"}
```

For the full upload format including `references/` files, see the live docs (`shared/live-sources.md` → Skills) — the wire format isn't fully cached.

### Skills vs the system prompt

Don't put domain knowledge in the system prompt if it could be a skill. Three reasons:

1. **Token efficiency.** The full skill content only loads when relevant. The system prompt is in every request.
2. **Reusability.** Skills attach to multiple agents. System prompts don't.
3. **Iteration.** Updating a skill creates a version; the agent picks it up automatically. Updating a system prompt requires re-running `setup.py` and re-versioning the agent.

System prompt = persona, operating principles, how this agent should *be*. Skills = how to do specific things.

## Memory stores — cross-session state

Sessions are ephemeral. Memory stores persist text documents across sessions in a workspace-scoped collection. Mounted into the container as a filesystem directory; the agent reads/writes via the standard file tools (`bash`, `read`, `write`, `edit`, `glob`, `grep`) — no dedicated memory tool.

Beta header `managed-agents-2026-04-01` (set automatically).

```python
store = client.beta.memory_stores.create(
    name="User Preferences",
    description="Per-user preferences and project context.",
)

# Optionally seed content host-side before any session runs:
client.beta.memory_stores.memories.create(
    store.id,
    path="/formatting_standards.md",
    content="All reports use GAAP formatting. Dates are ISO-8601...",
)
```

Attach at session-create time (only):

```python
session = client.beta.sessions.create(
    agent=AGENT_ID,
    environment_id=ENV_ID,
    resources=[
        {
            "type": "memory_store",
            "memory_store_id": store.id,
            "access": "read_write",   # or "read_only"; default read_write
            "instructions": "User preferences. Check before starting tasks.",
        }
    ],
)
```

The mount appears at `/mnt/memory/<store-name>/`. The agent finds files via the standard tools; a system-prompt note tells it the mount exists and what's in it (from the store's `description` and the resource's `instructions`).

**Max 8 memory stores per session.** Common reasons to use multiple:
- A read-only shared-reference store + a read-write per-user store
- One store per end-user/team/project sharing a single agent config

### Memory mutations and versions

Every mutation (create, update, delete) creates an immutable `memver_...` snapshot. This gives you:

- **Audit:** who changed what, when, from which session
- **Rollback:** read a prior version's content; write it back
- **Redaction:** scrub a version's content while preserving the audit trail (for leaked secrets, GDPR)

```python
# List versions for a memory
for v in client.beta.memory_stores.memory_versions.list(store.id, memory_id=mem.id):
    print(f"{v.id}: {v.operation}")  # created | modified | deleted

# Retrieve content of a specific version
v = client.beta.memory_stores.memory_versions.retrieve(version_id, memory_store_id=store.id)
print(v.content)

# Redact content from a version (preserves actor + timestamps)
client.beta.memory_stores.memory_versions.redact(version_id, memory_store_id=store.id)
```

### Optimistic concurrency

For host-side updates that need to avoid clobbering a concurrent writer:

```python
mem = client.beta.memory_stores.memories.retrieve(memory_id, memory_store_id=store.id)
client.beta.memory_stores.memories.update(
    mem.id,
    memory_store_id=store.id,
    content="updated body",
    precondition={"type": "content_sha256", "content_sha256": mem.content_sha256},
)
```

On mismatch the API returns 409 (`memory_precondition_failed_error`) — re-read and retry.

## Memory vs file resources — when to use which

| | Memory store | File resource |
|---|---|---|
| Lifetime | **Persistent across sessions** | This session only |
| Mutations | Versioned, audited | Read-only mount of a fixed file |
| Mount path | `/mnt/memory/<store>/` (FUSE) | Wherever you specify |
| Use for | User preferences, accumulated notes, learned context | One-shot data: the CSV to analyze, the doc to summarize |

If the data is "this is what we know about the user/project across all interactions," that's a memory store. If it's "this is what to look at right now," that's a file resource.

## Skills vs memory — when to use which

| | Skill | Memory store |
|---|---|---|
| Direction | The agent **reads** | The agent **reads and writes** |
| Lifetime | Versioned, edited via Skills API | Mutated turn by turn during sessions |
| Stability | Stable best practices, patterns, recipes | Evolving state, observations, decisions |

Skills are how the agent *gets better at things* over time, edited by humans. Memory is how the agent *remembers things* over time, edited by itself (and audited by humans).

## Common confusions

- **"Why doesn't the agent see my SKILL.md?"** — Did you re-run `setup.py` after editing `agent.yaml`? Skills attach at agent-create / agent-update; the agent doesn't auto-discover.
- **"How do I add a memory store after the session starts?"** — You can't. Memory stores attach at `sessions.create()` only (`resources.add()` doesn't accept `memory_store`).
- **"Can the agent edit my skills?"** — No. Skills are read-only from the agent's perspective. To author skills as a side-effect of agent runs, write the content to a memory store and have a human review + promote to a skill via the Skills API.
- **"What's a `MemoryPrefix`?"** — When you `list` memories with a hierarchical view, directory-like nodes come back as `{type: "memory_prefix", path}`. Scope your list with `path_prefix="/notes/"` (trailing slash matters) to filter.
