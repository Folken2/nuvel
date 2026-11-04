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
pip install -r requirements-dev.txt
cp .env.example .env
# add your OPENROUTER_API_KEY
```

One command installs everything: the CLI in editable mode, the runtime dependencies from
`requirements.txt` (including `nuvel-mcp`), and the test extras.

Contributors using `uv` can instead run `uv sync --all-extras`.

### Where `nuvel-mcp` comes from

The Skills MCP server is its own repository, [Folken2/nuvel-mcp](https://github.com/Folken2/nuvel-mcp),
with its own CI and releases; change it there, not here. `pyproject.toml` reads the project's
dependencies from `requirements.txt` (`dependencies = { file = ["requirements.txt"] }`), so that
file is the *published* dependency list. It installs `nuvel-mcp` from a release tag
(`nuvel-mcp @ git+https://github.com/Folken2/nuvel-mcp@<tag>`) until the package is on PyPI.
To move to a newer `nuvel-mcp`, change the tag in both `requirements.txt` and
`requirements-dev.txt`.

Do **not** add a local path to `requirements.txt` — it would land in the published metadata and
break consumers of the released package.

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

1. Builds sdist + wheel for `nuvel-cli` and verifies the tag matches the `version` in `pyproject.toml` (`nuvel-mcp` is released from its own repository)
2. Publishes to PyPI through the OIDC-backed `pypi` environment
3. Creates a GitHub release with auto-generated notes

To cut a release:

```bash
# 1. Bump version in pyproject.toml
# 2. Commit, push, and tag
git commit -am "release: 0.2.0"
git tag v0.2.0
git push && git push --tags
```

One-time setup before the first release:

- Register `nuvel` on PyPI and add this repo as a Trusted Publisher
  (`pypi.org` → Account → Publishing → Add a new pending publisher).
- Create a GitHub environment named `pypi` (Settings → Environments → New
  environment).

### `nuvel-mcp` must be on PyPI first

PyPI rejects uploads whose dependencies are direct URLs, and `requirements.txt`
currently installs `nuvel-mcp` from a git tag. Before the next `nuvel-cli`
release, `nuvel-mcp` has to be published to PyPI from its own repository and
the git line in `requirements.txt` replaced with a version range (for example
`nuvel-mcp>=0.5,<0.6`).

## Adding a new messaging-app channel

Channels live as overlays under `nuvel/backends/adk/templates_overlays/gateway-*/`.
A new channel needs:

1. `gateway-<name>/{{agent_package}}/gateways/<name>.py` — FastAPI APIRouter (or a
   sidecar module if the channel's SDK is incompatible with FastAPI, like Teams).
2. A `--with-<name>` flag added to `_add_new_agent_args` in `nuvel/cli.py`, with
   an `(adk only)` marker opening its help text, and threaded through
   `scaffold_agent()` in `nuvel/backends/adk/scaffold.py`. It also needs a row in
   `README.md`, in `docs/reference/cli.md` and in the
   [flag table](docs/how-to/pick-a-backend.md#nuvel-feature-support); the docs
   tests compare all of them with what the scaffolders accept.
3. ENV / README contributions in `_build_replacements` (see how `_TELEGRAM_ENV_BLOCK`
   is wired).
4. Tests under `tests/test_gateway_<name>.py` and a scaffold-flag test in
   `tests/test_scaffold_gateways.py`.
5. No change to the other backends. `nuvel agent create` refuses any flag a
   backend's `scaffold_agent()` does not declare, naming the flag and the backend.

## License

By contributing, you agree that your contributions will be licensed under the
MIT License (see [LICENSE](LICENSE)).
