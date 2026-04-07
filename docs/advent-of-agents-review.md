# Advent of Agents Review — Opportunities for Meta-Agent

> **Date:** 2026-04-07
> **Source:** [adventofagents.com](https://adventofagents.com/) — Google Cloud's 25-day program (Dec 2025)
> **Purpose:** Identify patterns and ideas to improve our meta-agent and the agents it generates.

---

## 1. What Is Advent of Agents?

Google Cloud's 25-day educational initiative teaching developers to build **production-ready AI agents** using ADK, Agent Engine, and Gemini. Each day covers one feature with copy-paste commands.

### Full Day List

| Day | Topic | Relevance |
|-----|-------|-----------|
| 1 | Introduction — Your First Agent | — |
| 2 | Build an agent in 5 min with YAML (no code) | Low |
| 3 | Agent Tools & Function Calling | Already covered |
| 4 | Multi-Agent Hierarchies | Already covered |
| 5 | **Durable Execution with Restate** | **HIGH** |
| 6–7 | Iterative Building & Testing | Moderate |
| 8 | **Context as Compiled View (stateful systems)** | **HIGH** |
| 9 | Memory & State Management | Already covered |
| 10 | Advanced Prompting Patterns | Already covered |
| 11 | **Managed MCP Connections** | **HIGH** |
| 12 | **Bi-Directional Streaming (Voice/Video)** | **HIGH** |
| 13 | **Interactions API (stateful autonomous workflows)** | **HIGH** |
| 14 | **Agent2Agent (A2A) Protocol** | **HIGH** |
| 15 | **A2UI — Agent-to-User Interface (generative UI)** | **MEDIUM** |
| 16 | LangGraph agents with A2A via Agent Starter Pack | Low |
| 17 | Gemini thinking levels & granular controls | Moderate |
| 18 | **Cloud API Registry (centralized tool management)** | **MEDIUM** |
| 19 | Register agent to Gemini Enterprise | Low |
| 20 | A2A Extensions | Moderate |
| 21 | Hall of Fame — competition winners | Inspiration |
| 22 | **Model Armor — Security Governance** | **HIGH** |
| 23 | **Durable Execution deep-dive (Restate)** | **HIGH** |
| 24 | Layering A2A onto existing agents | Moderate |
| 25 | Wrap-up | — |

---

## 2. Key Patterns to Adopt

### 2.1 Five Agent Skill Design Patterns ⭐⭐⭐

Source: [5 Agent Skill Design Patterns Every ADK Developer Should Know](https://lavinigam.com/posts/adk-skill-design-patterns/)

Our meta-agent already uses SkillToolset with progressive disclosure (L1/L2/L3). But the **5 canonical skill patterns** give us a taxonomy to teach generated agents:

| Pattern | What It Does | How We Can Use It |
|---------|-------------|-------------------|
| **Tool Wrapper** | Encodes library/API best practices into a skill; loads docs from `references/` | Generated agents should get a Tool Wrapper skill for their domain (e.g., a Stripe agent gets stripe-best-practices) |
| **Generator** | Produces structured docs from reusable templates (fill-in-the-blank) | Meta-agent can create Generator skills for report-building agents, email drafters, etc. |
| **Reviewer** | Scores output against a checklist stored in `references/review-checklist.md` | Every generated agent could include a self-review skill that checks its own output quality |
| **Inversion** | Agent interviews the user before acting (asks clarifying questions) | Our meta-agent already does this in Step 1 (Discovery). We should teach generated agents to do the same via a skill |
| **Pipeline** | Enforces strict multi-step workflow with diamond-gate checkpoints | Critical for generated agents that handle sensitive workflows (e.g., deployment, financial operations) |

**Action items:**
- [ ] Add an `adk-skill-design-patterns` skill to meta-agent's knowledge base covering all 5 patterns
- [ ] During generation (Step 2: Design), have meta-agent recommend which skill patterns fit the user's use case
- [ ] Include a template Reviewer skill in generated agents for self-assessment

---

### 2.2 Context as Compiled View ⭐⭐⭐

Source: [Day 8 — Context Engineering](https://google.github.io/adk-docs/context/)

**Key insight:** Context is NOT raw chat history — it's a **compiled view** over a richer stateful system:

```
Sources (sessions, memory, artifacts)
    ↓ Flows & Processors (compiler pipeline)
    ↓ Working Context (optimized prompt for this turn)
```

**What we already have:**
- `InstructionProvider` dynamically assembles SOUL.md + context files + memory
- `context_filter_plugin.py` limits invocations in context window

**What we're missing:**
- **Context compression/compaction** — ADK now has built-in context compaction that summarizes older turns to keep context manageable
- **Artifact-aware context** — treating uploaded files as first-class context sources
- **Explicit compilation pipeline** — our InstructionProvider is a single function; it should be a pipeline of composable processors

**Action items:**
- [ ] Add context compaction support to generated agents (use ADK's built-in `ContextCompactionProcessor`)
- [ ] Document the "context as compiled view" mental model in the `adk-prompt-engineering` skill
- [ ] Add a context budget calculator that estimates token usage across SOUL.md + contexts + memory + history

---

### 2.3 Voice / Bi-Directional Streaming Agents ⭐⭐⭐

Source: [ADK Gemini Live API Toolkit](https://google.github.io/adk-docs/streaming/), [DeepLearning.AI Course](https://www.deeplearning.ai/short-courses/building-live-voice-agents-with-googles-adk/)

ADK now supports **real-time voice agents** with:
- **LiveRequestQueue** — asyncio FIFO buffer decoupling WebSocket input from agent processing
- **run_live()** — async runner that consumes from the queue in real-time
- **Voice Activity Detection (VAD)** — automatic turn-taking
- **Multi-modal streaming** — audio, video, and text simultaneously
- **Tool calling during streams** — agent can call tools mid-conversation

**Architecture:**
```
Browser (mic/cam) → WebSocket → FastAPI → LiveRequestQueue → Agent (run_live)
                  ← WebSocket ← SSE/Stream ← Agent Response ←
```

**What we need to add:**
- A new **streaming template** for generated agents that want voice capabilities
- Support in `scaffold_tool.py` for a `--streaming` flag
- A `voice-agent-patterns` skill teaching how to build streaming agents

**Action items:**
- [ ] Create an `adk-streaming` skill documenting LiveRequestQueue, run_live, and audio/video patterns
- [ ] Add a streaming-capable `run_adk.py` template variant with WebSocket endpoints
- [ ] Add `streaming` as an agent capability option during Step 1 (Discovery)

---

### 2.4 RAG Without Vector Databases (LLM-Native RAG) ⭐⭐

Source: [Beyond Vector Databases](https://www.digitalocean.com/community/tutorials/beyond-vector-databases-rag-without-embeddings), [Anthropic Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)

**Key insight:** For many agents, you don't need a vector DB at all. Alternatives:

| Approach | When to Use | How It Works |
|----------|-------------|--------------|
| **Context Stuffing** | Knowledge base < 200K tokens (~500 pages) | Include entire KB in prompt |
| **Keyword/BM25 Search** | Structured docs, exact term matching | Traditional search, no embeddings |
| **Agentic RAG** | Complex queries needing multi-step reasoning | Agent searches, reads, reasons, searches again |
| **Karpathy's LLM Wiki** | Mid-size KB (hundreds to ~10K docs) | LLM maintains a structured wiki of interlinked .md files |
| **Knowledge Graphs** | Relational data, entity-centric queries | Graph traversal instead of similarity search |
| **Google Search Grounding** | Real-time/current information | Use Google Search as a built-in tool |

#### Karpathy's LLM Knowledge Base (No Embeddings, No Vector DB)

Source: [Karpathy's GitHub Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), [VentureBeat](https://venturebeat.com/data/karpathy-shares-llm-knowledge-base-architecture-that-bypasses-rag-with-no)

**The problem with traditional RAG:** It rediscovers knowledge from scratch on every question — there is no accumulation. Embeddings are a "black box" that humans can't read or edit.

**Karpathy's alternative:**
1. **Ingest:** Raw materials (papers, articles, repos, notes) dumped into `raw/` as Markdown
2. **LLM Processing:** The LLM ingests new content, updates relevant wiki pages, maintains cross-references, and creates index/summary files
3. **Health Checks:** Periodic "linting" passes where the LLM scans the wiki for inconsistencies or missing connections
4. **Query Time:** Instead of vector similarity search, the LLM navigates via summaries and index files

**Why this matters for our meta-agent:**
- Our `contexts/` directory + `memory/` system is *already close* to this pattern
- We could evolve it into a proper "LLM Wiki" where agents maintain their own structured knowledge
- Every claim is traceable to a specific `.md` file a human can read/edit/delete
- At ~100 articles / ~400K words, LLM navigation via summaries is more than sufficient

**What we already have:**
- Context files (`contexts/*.md`) — basically context stuffing
- Memory system (`memory/AGENT_MEMORY.md` + `topics/`) — markdown-based persistent state
- This is already 70% of Karpathy's architecture!

**What we're missing:**
- Automated **ingest pipeline** that processes raw docs into structured wiki pages
- **Index/summary files** that let the agent navigate large KBs efficiently
- **Health check / linting** passes for knowledge consistency
- **Cross-referencing** between wiki pages

**What we can improve:**
- For agents with large knowledge bases, offer a **tiered RAG strategy**:
  1. Small KB (< 100 pages): Context stuffing via `contexts/` directory (current approach)
  2. Medium KB (100-500 pages): LLM Wiki pattern — agent maintains structured .md files with indexes + cross-references
  3. Large KB (500+ pages): Vertex AI RAG Engine integration or Google Search grounding

**Action items:**
- [ ] Add a `rag-patterns` skill covering embedding-free RAG approaches, especially Karpathy's LLM Wiki
- [ ] During Step 1 (Discovery), ask about knowledge base size and recommend appropriate RAG strategy
- [ ] Add Google Search grounding as a default tool option for information-seeking agents
- [ ] Add an `ingest_document` tool template that processes raw docs into structured wiki pages
- [ ] Add index/summary generation to the memory system for agents with large context directories
- [ ] Add a simple keyword search tool template for medium-sized knowledge bases

---

### 2.5 Durable Execution (Restate Integration) ⭐⭐⭐

Source: [Restate + Google ADK](https://www.restate.dev/blog/build-resilient-ai-agents-with-restate-and-google-adk)

**Problem:** Agents crash mid-execution, lose context, and can't resume long-running tasks.

**Solution:** Restate provides durable execution:
- **Automatic recovery** — if agent crashes, Restate replays the execution journal to the failure point
- **Persistent state** — conversation history survives process restarts
- **Observable execution** — full audit trail of every step
- **Long-running tasks** — agents can pause execution for days and resume

**How it works:**
```
Agent Step 1 (recorded) → Agent Step 2 (recorded) → CRASH
                                                      ↓
Restate replays journal → Skip Step 1 → Skip Step 2 → Resume Step 3
```

**What we already have:**
- `resilience_plugin.py` — circuit breaker + rate limiting (but no durable execution)
- `trace_plugin.py` — observability (JSONL + Postgres)

**What's missing:**
- No crash recovery — if the process dies, all state is lost
- No execution journal / replay capability
- No long-running task support (pause/resume)

**Action items:**
- [ ] Create an `adk-durable-execution` skill documenting Restate integration patterns
- [ ] Add Restate as an optional infrastructure component in the template
- [ ] For production agents, recommend durable execution during Step 2 (Design)
- [ ] Add `restate` to the requirements.txt template as an optional dependency

---

### 2.6 Model Armor — Security Governance ⭐⭐

Source: [Model Armor](https://cloud.google.com/security/products/model-armor), [Building a secure agent system](https://codelabs.developers.google.com/secure-agent-modelarmor)

**What Model Armor does:**
- **Pre-inference scanning** — inspects prompts for jailbreaks and injections before hitting the model
- **PII detection** — identifies and redacts 150+ PII types (credit cards, SSNs, API keys)
- **Content safety** — blocks harmful content generation
- **MCP integration** — protects against tool poisoning in MCP connections
- **Plugin-based** — configure once, apply to every agent

**What we already have:**
- `cost_guard_plugin.py` — budget enforcement (cost, not security)
- No prompt injection detection
- No PII filtering
- No content safety guardrails

**Action items:**
- [ ] Add a `security-guardrails` skill documenting Model Armor and safety patterns
- [ ] Create a `safety_plugin.py` template that integrates Model Armor (or a local equivalent)
- [ ] During Step 2 (Design), ask about security requirements and recommend guardrails
- [ ] Add basic input sanitization to the default tool template

---

### 2.7 Agent2Agent (A2A) Protocol ⭐⭐

Source: [Day 14 — A2A](https://adventofagents.com/day/14)

**What A2A enables:**
- Agents communicating across teams, frameworks, and languages
- Standardized protocol for inter-agent communication
- Discovery via Agent Cards
- Can be layered onto existing agents with a single flag (Day 24)

**Relevance:** Our generated agents are standalone. A2A would let them:
- Delegate subtasks to specialized agents
- Be discovered by other agents in an organization
- Participate in multi-agent workflows without tight coupling

**Action items:**
- [ ] Add an `adk-a2a-protocol` skill documenting A2A patterns
- [ ] Generate A2A-compatible Agent Cards for every scaffolded agent
- [ ] Add A2A server endpoint as an optional capability in the template

---

### 2.8 A2UI — Agent-to-User Interface ⭐

Source: [Day 15 — A2UI](https://adventofagents.com/)

**What A2UI does:** Agents stream dynamic, generative UIs as JSONL payloads — not just text, but actual interactive UI components.

**Relevance:** Our generated agents currently return text/JSON. A2UI would let them return rich interactive elements (charts, forms, buttons).

**Action items:**
- [ ] Monitor A2UI development; add skill when the API stabilizes
- [ ] Consider adding a `ui_response` utility to generated agents

---

## 3. Priority Ranking

| Priority | Pattern | Impact | Effort |
|----------|---------|--------|--------|
| **P0** | 5 Skill Design Patterns | High — directly improves generated agent quality | Low — skill docs only |
| **P0** | Context as Compiled View | High — better context management = better agents | Medium — template changes |
| **P1** | Voice/Streaming Support | High — unlocks new agent category | High — new template + skill |
| **P1** | Durable Execution (Restate) | High — production reliability | Medium — optional integration |
| **P1** | Model Armor / Security | High — production readiness | Medium — new plugin |
| **P2** | RAG Patterns | Medium — most agents don't need heavy RAG | Low — skill docs |
| **P2** | A2A Protocol | Medium — multi-agent future | Medium — template changes |
| **P3** | A2UI | Low (early stage) — monitor | Low |

---

## 4. Quick Wins (Can Do Now)

1. **Add the 5 Skill Design Patterns to meta-agent's knowledge** — Create a new skill `adk-skill-design-patterns` with the Tool Wrapper, Generator, Reviewer, Inversion, and Pipeline patterns. This immediately improves the quality of skills the meta-agent generates.

2. **Add context compaction awareness** — Update the `adk-prompt-engineering` skill to document context compaction and the "compiled view" mental model.

3. **Add Google Search grounding as a default tool option** — For any information-seeking agent, suggest `google_search` as a built-in ADK tool.

4. **Add a self-review skill template** — Every generated agent gets a Reviewer-pattern skill that checks its own output quality before responding.

5. **Recommend Inversion pattern for complex agents** — Agents that handle ambiguous requests should interview the user first (our meta-agent already does this, but generated agents don't).

---

## 5. Medium-Term Improvements

1. **Streaming template** — A variant of `run_adk.py` and `agent.py` that supports WebSocket + LiveRequestQueue for voice agents.

2. **Durable execution integration** — Optional Restate dependency for agents that need crash recovery and long-running task support.

3. **Security plugin** — A `safety_plugin.py` that provides basic prompt injection detection and PII filtering, with optional Model Armor integration for Google Cloud deployments.

4. **A2A Agent Cards** — Auto-generate an `agent_card.json` for every scaffolded agent, making it discoverable by other agents.

---

## Sources

- [Advent of Agents — Google Cloud](https://adventofagents.com/)
- [5 Agent Skill Design Patterns](https://lavinigam.com/posts/adk-skill-design-patterns/)
- [Developer's Guide to Building ADK Agents with Skills](https://developers.googleblog.com/developers-guide-to-building-adk-agents-with-skills/)
- [ADK Gemini Live API Toolkit](https://google.github.io/adk-docs/streaming/)
- [Building Live Voice Agents with Google's ADK](https://www.deeplearning.ai/short-courses/building-live-voice-agents-with-googles-adk/)
- [Beyond Vector Databases: RAG Without Embeddings](https://www.digitalocean.com/community/tutorials/beyond-vector-databases-rag-without-embeddings)
- [Restate + Google ADK](https://www.restate.dev/blog/build-resilient-ai-agents-with-restate-and-google-adk)
- [Model Armor — Google Cloud](https://cloud.google.com/security/products/model-armor)
- [ADK Context Engineering](https://google.github.io/adk-docs/context/)
- [Karpathy's LLM Knowledge Base Gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [Microsoft Vectorless Reasoning-Based RAG (PageIndex)](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/vectorless-reasoning-based-rag-a-new-approach-to-retrieval-augmented-generation/4502238)
- [Grokipedia — Advent of Agents](https://grokipedia.com/page/Advent_of_Agents)
- [HowAIWorks — Advent of Agents 2025](https://howaiworks.ai/blog/google-cloud-advent-of-agents-2025)
