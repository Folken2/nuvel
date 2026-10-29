# Contributing to nuvel

Thanks for your interest in contributing. This project is maintained by a single
developer, so please read this before opening a PR — it will save us both time.

## Scope

nuvel generates production-ready Google ADK agents from natural language.
In-scope contributions:

- Bug fixes in the scaffolder, generated-agent templates, or plugin chain
- New skill templates or tool generators that apply broadly
- Documentation, examples, and tests
- Performance and reliability improvements

Out-of-scope (open an issue to discuss first):

- Rewrites or large architectural changes
- Dependencies on new LLM providers beyond OpenRouter
- Features tied to a specific use case (fork instead)

## Development setup

```bash
git clone https://github.com/Folken2/nuvel.git
cd nuvel
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# add your OPENROUTER_API_KEY
```

Contributors using `uv` can instead run `uv sync --all-extras` to install the
project with every extra (the `nuvel-mcp` package is a workspace member).

Run the test suite:

```bash
pytest
```

## Pull request checklist

- [ ] Branch off `main`, keep the PR focused on one change
- [ ] Tests pass locally (`pytest`)
- [ ] New behavior is covered by a test
- [ ] No secrets, `.env` files, or personal notes in the diff
- [ ] Commit messages follow the existing style (`feat:`, `fix:`, `docs:`, …)
- [ ] PR description explains *why*, not just *what*

## Reporting bugs

Open a GitHub issue with:

- What you expected to happen
- What actually happened (include stack trace if any)
- Minimum command or description that reproduces it
- Your Python version and OS

## Reporting security issues

Do **not** open a public issue. See [SECURITY.md](SECURITY.md).

## Code of conduct

Be kind. Assume good faith. Technical disagreement is welcome; personal attacks
are not.

## Releasing (maintainers)

nuvel publishes to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — no
API tokens stored in GitHub secrets. The workflow in
[.github/workflows/release.yml](.github/workflows/release.yml) triggers on a
`v*.*.*` tag push and:

1. Builds sdist + wheel for both `nuvel-cli` and `nuvel-mcp` and verifies the tag matches the `version` in both `pyproject.toml` and `packages/nuvel-mcp/pyproject.toml` (the two packages always release at the same version)
2. Publishes to PyPI through the OIDC-backed `pypi` environment
3. Creates a GitHub release with auto-generated notes

To cut a release:

```bash
# 1. Bump version in pyproject.toml AND packages/nuvel-mcp/pyproject.toml
#    and move the nuvel-mcp range in requirements.txt to match
#    (e.g. 0.6.0 -> nuvel-mcp>=0.6.0,<0.7)
# 2. Commit, push, and tag
git commit -am "release: 0.2.0"
git tag v0.2.0
git push && git push --tags
```

One-time setup before the first release:

- Register `nuvel` on PyPI and add this repo as a Trusted Publisher
  (`pypi.org` → Account → Publishing → Add a new pending publisher).
- Do the same for `nuvel-mcp`: the PyPI project does not exist yet, so add a
  *pending publisher* for it (same repo, workflow `release.yml`, environment
  `pypi`). This is a manual maintainer step outside the repo.
- Create a GitHub environment named `pypi` (Settings → Environments → New
  environment).

### Before merging the `nuvel-mcp` branch

`nuvel-cli` depends on `nuvel-mcp`, a name nobody has registered on PyPI yet.
Before the branch that introduces it is pushed or merged, a maintainer must
publish `nuvel-mcp` `0.5.0.dev0` (or a placeholder) through the pending
publisher above, so no one else can claim the name in between
(dependency confusion). Keep the compatible range pin
(`nuvel-mcp>=0.5.0.dev0,<0.6`); do not switch to an exact pin.

## Adding a new messaging-app channel

Channels live as overlays under `nuvel/backends/adk/templates_overlays/gateway-*/`.
A new channel needs:

1. `gateway-<name>/{{agent_package}}/gateways/<name>.py` — FastAPI APIRouter (or a
   sidecar module if the channel's SDK is incompatible with FastAPI, like Teams).
2. A `--with-<name>` flag added to `nuvel/cli.py` and threaded through
   `scaffold_agent()` in `nuvel/backends/adk/scaffold.py`.
3. ENV / README contributions in `_build_replacements` (see how `_TELEGRAM_ENV_BLOCK`
   is wired).
4. Tests under `tests/test_gateway_<name>.py` and a scaffold-flag test in
   `tests/test_scaffold_gateways.py`.
5. The same flag added to the rejection lists in
   `nuvel/backends/claude_agent_sdk/scaffold.py` and
   `nuvel/backends/anthropic_managed_agents/scaffold.py`.

## License

By contributing, you agree that your contributions will be licensed under the
MIT License (see [LICENSE](LICENSE)).
