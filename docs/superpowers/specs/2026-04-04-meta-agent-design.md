# Meta-Agent Design Spec

> A production-ready ADK agent that creates production-ready ADK agents.

## Core Principle

Don't reinvent the wheel. The proven data-analysis-agent infrastructure (plugins, runner, resilience, sessions) becomes a reusable skeleton. The meta-agent only generates the brain: prompts, tools, and skills.

## Architecture

```
meta-agent/
├── meta_agent/                      # The meta-agent itself
│   ├── __init__.py                  # Exports root_agent
│   ├── agent.py                     # LlmAgent wired with tools + SkillToolset
│   ├── prompt/
│   │   ├── __init__.py
│   │   └── instructions.py          # System prompt: "you are an agent builder"
│   ├── tools/
│   │   ├── __init__.py              # Tool registry
│   │   ├── scaffold_tool.py         # Runs scaffold.py to stamp out skeleton
│   │   ├── file_tools.py            # write_file, read_file, list_files (scoped)
│   │   └── validate_tool.py         # Agent structure validation
│   ├── skills/                      # ADK knowledge loaded via SkillToolset
│   │   ├── adk-agent-patterns/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   ├── adk-prompt-engineering/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   ├── adk-tool-creation/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   ├── adk-skill-creation/
│   │   │   ├── SKILL.md
│   │   │   └── references/
│   │   └── adk-callbacks-hitl/
│   │       ├── SKILL.md
│   │       └── references/
│   ├── templates/                   # Sanitized skeleton for new agents
│   │   ├── __init__.py
│   │   ├── agent.py.stub
│   │   ├── run_adk.py
│   │   ├── requirements.txt
│   │   ├── .env.example
│   │   ├── config/
│   │   ├── plugins/
│   │   ├── callbacks/
│   │   ├── utils/
│   │   ├── state/
│   │   ├── prompt/                  # Stub instruction provider
│   │   ├── tools/                   # Stub tool registry
│   │   ├── skills/                  # Empty skills directory
│   │   └── contexts/               # Empty context directory
│   ├── callbacks/
│   │   └── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── llm.py                   # LiteLLM/OpenRouter (default for code gen)
│   │   └── logging.py
│   ├── plugins/                     # Same plugin chain (minus DB-specific)
│   │   └── __init__.py
│   ├── state/
│   │   └── __init__.py
│   └── utils/
│       └── __init__.py
├── generated-agents/                # Default output directory
├── run_adk.py                       # FastAPI runner (same pattern)
├── scaffold.py                      # Skeleton stamping script
├── requirements.txt
├── .env.example
└── README.md
```

## Workflow

The meta-agent follows this workflow when creating a new agent:

1. **Discovery** - Ask the user about: agent goal, target tasks, required tools/integrations, domain knowledge, desired LLM model
2. **Design** - Propose the agent architecture: which tools to create, which skills to write, prompt strategy
3. **Scaffold** - Call `scaffold_agent` tool to stamp out the skeleton
4. **Generate** - Load relevant skills via SkillToolset and write the custom files:
   - `prompt/instructions.py` - System prompt tailored to the agent's goal
   - `tools/*.py` + `tools/__init__.py` - Custom function tools
   - `skills/*/SKILL.md` - Domain-specific skills with references
   - `contexts/*.md` - Domain knowledge files
   - `agent.py` - Wires tools + SkillToolset + prompt
   - `.env.example` - Documents all required env vars
5. **Validate** - Call `validate_agent` to verify structure and imports
6. **Iterate** - Present the result, accept feedback, refine

## Tools

### Function Tools (file operations)

| Tool | Signature | Purpose |
|------|-----------|---------|
| `scaffold_agent` | `(name: str, output_dir: str = None) -> dict` | Runs scaffold.py to copy skeleton, rename package, create stubs |
| `write_file` | `(path: str, content: str) -> dict` | Writes/overwrites a file. Path is relative to agent output dir. Rejects paths outside it |
| `read_file` | `(path: str) -> dict` | Reads a file from the agent output dir |
| `list_files` | `(path: str = ".") -> dict` | Lists files/dirs in the agent output dir |
| `validate_agent` | `(name: str) -> dict` | Checks: required files exist, imports resolve, agent can load, skills are valid |

All file tools are **scoped to the output directory** - they reject absolute paths or `../` traversal.

### SkillToolset (ADK knowledge)

The meta-agent uses `SkillToolset` from `google.adk.tools.skill_toolset` to load its own domain knowledge on demand. This provides three auto-generated tools:

- `list_skills` (L1) - Returns metadata for all 5 skills. Injected on every call.
- `load_skill` (L2) - Loads full instructions for a specific skill.
- `load_skill_resource` (L3) - Loads a reference file from a skill's `references/` dir.

#### Skills

**1. adk-agent-patterns**
- Description: Agent architecture patterns - when/how to use LlmAgent, LoopAgent, SequentialAgent, ParallelAgent, and multi-agent hierarchies.
- L3 References: `loop-patterns.md`, `parallel-patterns.md`, `multi-agent-patterns.md`

**2. adk-prompt-engineering**
- Description: Writing effective ADK system prompts - dynamic instructions, state placeholders, InstructionProvider pattern, context injection.
- L3 References: `prompt-templates.md`, `instruction-provider-pattern.md`, `state-placeholders.md`

**3. adk-tool-creation**
- Description: Building ADK function tools - ToolContext usage, error handling, type hints, return formats, async patterns.
- L3 References: `tool-patterns.md`, `tool-context-api.md`, `async-tool-examples.md`

**4. adk-skill-creation**
- Description: Creating valid SKILL.md files following the agentskills.io spec - frontmatter, instructions, references, progressive disclosure.
- L3 References: `skill-spec.md`, `example-skills.md`, `skilltoolset-wiring.md`

**5. adk-callbacks-hitl**
- Description: Callback signatures, HITL approval gates, state management with EventActions, before/after hooks.
- L3 References: `callback-signatures.md`, `hitl-patterns.md`, `state-management.md`

## Skeleton Template

### What the skeleton includes (copied as-is from data-analysis-agent):

- `run_adk.py` - FastAPI server, API key middleware, request ID tracing, health checks, plugin registration, graceful shutdown
- `plugins/` - Full plugin chain:
  - TracePlugin (structured logging/tracing)
  - ContextFilterPlugin (conversation history trimming)
  - ConsoleLoggerPlugin (ANSI terminal output)
  - ToolEventsPlugin (SSE streaming)
  - ResiliencePlugin (circuit breaker + rate limiting)
  - CachePlugin (TTL-based query caching)
  - ReflectAndRetryToolPlugin (self-healing retries)
- `config/llm.py` - LiteLLM/OpenRouter config with env-var-driven model selector
- `config/logging.py` - Structured logging setup
- `utils/resilience.py` - CircuitBreaker, RateLimiter
- `utils/date_utils.py` - Date/time helpers
- `state/query_cache.py` - Session-scoped caching
- `requirements.txt` - All production dependencies

### What gets stripped (DB-specific):

- `tools/query_tool.py`, `get_schema_tool.py`, `db_connection.py`, `query_validation.py`, `file_url_tool.py`
- `plugins/security_plugin.py` (Postgres validation)
- `plugins/query_analysis_plugin.py` (DB debugging hints)
- `prompt/schema_fetcher.py` (live schema injection)
- `contexts/celo.md` (app-specific)
- `skills/celo-torque-plots/` (app-specific)
- `callbacks/image_artifacts.py` (Composio-specific)

### What scaffold.py creates as stubs:

- `agent.py` - Minimal wiring with imports from `prompt/` and `tools/`
- `prompt/__init__.py` + `prompt/instructions.py` - Empty instruction provider
- `tools/__init__.py` - Empty tool registry
- `skills/` - Empty skills directory
- `contexts/` - Empty context directory
- `.env.example` - Env var template

### scaffold.py behavior:

```
scaffold.py <agent-name> [--output-dir ./generated-agents]
  1. Copies templates/ → <output-dir>/<agent-name>/
  2. Renames package: template_agent → <agent_name> (snake_case)
  3. Updates all internal imports
  4. Creates stub files for brain components
  5. Returns success with created file list
```

Agent name requirements: kebab-case, lowercase letters/digits/hyphens, max 40 chars, starts with letter, no trailing hyphen.

## System Prompt Strategy

The meta-agent's `prompt/instructions.py` defines:

**Identity:** You are a production-ready ADK agent builder. Given a goal, tasks, and tools, you create complete, runnable Google ADK agents.

**Enforced workflow:**
1. Discovery - Understand goal, tasks, tools, domain
2. Design - Propose architecture, get approval
3. Scaffold - Stamp out skeleton
4. Generate - Load skills, write custom files
5. Validate - Check structure and imports
6. Iterate - Refine based on feedback

**Key rules:**
- Always load skills before generating (don't hallucinate patterns)
- Generate tools with proper ToolContext signatures, error handling, type hints
- Generate skills following agentskills.io spec strictly
- Write prompts that use state placeholders where appropriate
- Include .env.example documenting all required env vars
- Never hardcode secrets or credentials
- Scope all file operations to the output directory

## Generated Agent Output

When the meta-agent creates a new agent, the output looks like:

```
generated-agents/k8s-monitor-agent/
├── k8s_monitor_agent/
│   ├── __init__.py                  # Exports root_agent
│   ├── agent.py                     # LlmAgent with tools + SkillToolset
│   ├── prompt/
│   │   ├── __init__.py
│   │   └── instructions.py          # K8s monitoring system prompt
│   ├── tools/
│   │   ├── __init__.py              # Registers k8s tools
│   │   ├── k8s_tools.py             # get_pod_status, get_pod_logs, etc.
│   │   └── ...
│   ├── skills/
│   │   └── k8s-alerting/
│   │       ├── SKILL.md             # Alerting patterns, thresholds
│   │       └── references/
│   │           └── escalation.md    # Escalation procedures
│   ├── contexts/
│   │   └── k8s.md                   # Kubernetes domain knowledge
│   ├── config/                      # LiteLLM config (inherited)
│   ├── plugins/                     # Full plugin chain (inherited)
│   ├── callbacks/
│   ├── utils/
│   └── state/
├── run_adk.py                       # FastAPI server (inherited)
├── requirements.txt                 # Production deps (inherited)
├── .env.example                     # OPENROUTER_API_KEY, agent-specific vars
└── README.md                        # Auto-generated docs
```

## LLM Configuration

- Default: OpenRouter via LiteLLM (configurable via `FAST_MODEL` env var)
- Generated agents inherit the same pattern
- Env var `FAST_MODEL` controls model selection at deploy time
- No model hardcoding in agent code

## Output Directory

- Env var: `AGENTS_OUTPUT_DIR` (default: `./generated-agents/`)
- Overridable per scaffold request
- All file tools scoped to this directory

## Phased Delivery

### V1 (current scope)
- Local file output
- scaffold.py script
- 5 meta-skills with L3 references
- File operation tools (scaffold, write, read, list, validate)
- Full plugin chain (inherited from data-analysis-agent)
- Validation tool

### V2 (future)
- `create_github_repo` tool
- `push_to_github` tool
- `setup_ci` tool (GitHub Actions workflow generation)
- Optional deployment triggers (Cloud Run, Vertex AI)

## Success Criteria

1. Meta-agent can create a complete, runnable ADK agent from a natural language description
2. Generated agents inherit the full production infrastructure (plugins, resilience, sessions)
3. Generated agents include proper system prompts, tools, and skills
4. Generated agents can be started with `adk web .` or `python run_adk.py` immediately
5. The meta-agent loads its own ADK knowledge via SkillToolset (progressive disclosure)
6. Generated agents use SkillToolset for their domain knowledge
7. All file operations are scoped to the output directory
8. validate_agent catches structural errors before the user tries to run
