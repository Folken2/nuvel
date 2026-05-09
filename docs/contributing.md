# Contributing

The canonical guide is [`CONTRIBUTING.md`](https://github.com/Folken2/nuvel/blob/main/CONTRIBUTING.md) in the repository root. Highlights below.

## Setup

```bash
git clone https://github.com/Folken2/nuvel.git
cd nuvel
python -m venv .venv && source .venv/bin/activate
pip install -e ".[docs]"        # editable install + MkDocs for docs site
```

## Running the test suite

```bash
pytest tests/ -q
```

213+ tests. CI runs the same command.

## Working on the docs site

```bash
# Live reload at http://127.0.0.1:8000
mkdocs serve

# Build static site (in ./site/)
mkdocs build
```

The site is published to GitHub Pages on every push to `main` via `.github/workflows/docs.yml`.

## Adding a new bundled skill

Drop the skill at `nuvel/backends/<framework>/skills/<slug>/SKILL.md` with the standard frontmatter:

```markdown
---
name: <slug>
description: <one-line trigger description>
---

# <Title>

<Body>
```

`nuvel skills list --framework <framework>` will pick it up.

## Adding a new messaging-app channel

Channels are overlays under `nuvel/backends/adk/templates_overlays/gateway-*/`. Five steps:

1. **Create the overlay file** at `gateway-<name>/{{agent_package}}/gateways/<name>.py` — a FastAPI APIRouter (or a sidecar module if the channel's SDK isn't FastAPI-compatible, like Teams).
2. **Add the flag** `--with-<name>` in `nuvel/cli.py` and thread it through `scaffold_agent()` in `nuvel/backends/adk/scaffold.py`.
3. **Wire the placeholders** in `_build_replacements` — at minimum, populate `gateway_imports_lines`, `gateway_mounts_lines` (skip if sidecar), `gateway_env_blocks`, and `gateway_readme_blocks`. Add a `_<NAME>_ENV_BLOCK` and `_<NAME>_README_BLOCK` constant.
4. **Write tests** under `tests/test_gateway_<name>.py` (router unit tests with FastAPI TestClient + mocked runner) and a scaffold-flag test class in `tests/test_scaffold_gateways.py`.
5. **Reject the flag in non-ADK backends.** Add the same flag to `nuvel/backends/claude_agent_sdk/scaffold.py` and `nuvel/backends/anthropic_managed_agents/scaffold.py`'s rejection lists.

For a concrete reference implementation, see commits on the [messaging-gateways feature branch history](https://github.com/Folken2/nuvel/pulls?q=is%3Apr+messaging-gateways) and the design spec at `docs/superpowers/specs/2026-05-09-messaging-gateways-design.md` in the repo.

## Working on a feature

The repo uses an isolated-worktree workflow under `.worktrees/`:

```bash
# from the main repo root
git worktree add .worktrees/feat-my-thing -b feat/my-thing
cd .worktrees/feat-my-thing
```

`.worktrees/` is gitignored. After merge, clean up with `git worktree remove .worktrees/feat-my-thing && git branch -d feat/my-thing` from the main repo root.

For non-trivial features, follow the spec → plan → execute pattern by checking in a design doc to `docs/superpowers/specs/YYYY-MM-DD-<feature>-design.md` and an implementation plan to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`. These are excluded from the published docs site but kept in the repo as durable design history.

## Issues and PRs

- File issues at [github.com/Folken2/nuvel/issues](https://github.com/Folken2/nuvel/issues).
- Run `pytest tests/ -q` before opening a PR.
- The CI workflow at `.github/workflows/tests.yml` runs the same suite.

## License

By contributing, you agree your contributions are MIT-licensed.
