# Contributing to Meta-Agent

Thanks for your interest in contributing. This project is maintained by a single
developer, so please read this before opening a PR — it will save us both time.

## Scope

Meta-Agent generates production-ready Google ADK agents from natural language.
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
git clone https://github.com/Folken2/meta-agent.git
cd meta-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# add your OPENROUTER_API_KEY
```

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

## License

By contributing, you agree that your contributions will be licensed under the
MIT License (see [LICENSE](LICENSE)).
