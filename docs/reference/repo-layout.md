# Repository layout

What lives where in the nuvel source tree.

```
nuvel/                                  # the Python package
├── cli.py                              # `nuvel` CLI entrypoint (argparse)
├── agent.py                            # the meta-agent's LlmAgent definition
├── run_adk.py                          # FastAPI server for the meta-agent
├── backends/
│   ├── adk/
│   │   ├── scaffold.py                 # `scaffold_agent()` for ADK
│   │   ├── templates/                  # base ADK template tree
│   │   │   ├── run_adk.py              # generated server entrypoint
│   │   │   ├── Dockerfile
│   │   │   ├── railway.json
│   │   │   ├── requirements.txt
│   │   │   ├── .env.example
│   │   │   ├── README.md.tmpl
│   │   │   └── {{agent_package}}/
│   │   │       ├── agent.py            # LlmAgent definition
│   │   │       ├── prompt/             # instruction frame
│   │   │       ├── tools/              # built-in + Composio + persona tools
│   │   │       ├── plugins/            # ADK plugins (cost guard, traces, etc.)
│   │   │       ├── callbacks/          # ADK callbacks
│   │   │       ├── config/             # logging, LLM model config
│   │   │       ├── state/              # state primitives
│   │   │       └── streaming.py        # WebSocket streaming
│   │   ├── templates_overlays/
│   │   │   ├── persona/                # --persona overlay
│   │   │   ├── composio/               # --with-composio overlay
│   │   │   ├── gateway-base/           # shared `_common.py` for Slack/Telegram
│   │   │   ├── gateway-slack/          # --with-slack overlay
│   │   │   ├── gateway-telegram/       # --with-telegram overlay
│   │   │   └── gateway-teams/          # --with-teams overlay (sidecar)
│   │   └── skills/                     # ADK-portable knowledge skills
│   ├── claude_agent_sdk/
│   │   ├── scaffold.py
│   │   ├── templates/                  # base Claude Agent SDK template
│   │   └── skills/
│   └── anthropic_managed_agents/
│       ├── scaffold.py
│       ├── templates/                  # base Anthropic Managed Agents template
│       └── skills/
├── prompt/                             # meta-agent's instruction frame
├── tools/                              # meta-agent's tools
├── skills/                             # meta-agent's bundled skills
├── plugins/                            # meta-agent's plugins
├── config/                             # logging, LLM config (used by meta-agent)
└── utils/

tests/                                  # pytest suite
docs/                                   # this site (reader-facing) +
└── superpowers/                        # internal specs/plans (ignored by mkdocs)
    ├── specs/                          # design docs (one per feature)
    └── plans/                          # implementation plans (one per feature)

generated-agents/                       # default output dir for `nuvel agent create` (gitignored)
.worktrees/                             # git worktrees for parallel feature work (gitignored)
```

## How the scaffold works

1. `nuvel cli new` calls `scaffold_agent()` in the chosen backend.
2. `scaffold_agent()` builds a replacements dict (`{{agent_name}}`, `{{agent_package}}`, plus per-overlay placeholders).
3. `_stamp_tree(TEMPLATES_DIR, target, replacements)` walks the base template, copying files, renaming `{{agent_package}}` directories to the snake-cased name, and substituting placeholders in text files.
4. For each enabled overlay, `_stamp_tree(OVERLAYS_DIR / "<overlay>", target, replacements)` runs again — overlays are additive, can override base files.

That's the entire mechanism. There's no runtime layer between a generated agent and the framework SDK.

## Where to add things

| To add… | Edit |
|---|---|
| A new framework backend | `nuvel/backends/<name>/{scaffold.py,templates/,skills/}` + register in `nuvel/cli.py::SUPPORTED_FRAMEWORKS` |
| A new ADK overlay (feature) | `nuvel/backends/adk/templates_overlays/<feature>/` + flag in `cli.py` + threading in `nuvel/backends/adk/scaffold.py::_build_replacements` and `scaffold_agent` + rejection in non-ADK scaffolders |
| A new bundled skill | `nuvel/backends/<framework>/skills/<slug>/SKILL.md` (+ helper files as needed) |
| A new messaging-app channel | See [Contributing](../contributing.md) — there's a 5-step checklist |

## Process artifacts

`docs/superpowers/specs/` and `docs/superpowers/plans/` hold the design docs and implementation plans for non-trivial features. They're committed to the repo for history but excluded from this published documentation site (see `mkdocs.yml::exclude_docs`).
