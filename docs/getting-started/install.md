# Install

## Requirements

- **Python 3.11 or newer.** Check with `python --version`.
- **A Unix-like shell.** Linux, macOS, or WSL. Windows native is untested.
- *(Optional)* **Docker** if you plan to ship the generated agent as a container.
- *(Optional)* **PostgreSQL** for production session storage (dev mode uses in-memory sessions).

## Install from PyPI

```bash
pip install nuvel-cli
```

This installs the `nuvel` CLI and everything needed to **scaffold** on any supported backend. Running what you scaffold takes a little more, depending on the backend:

| Backend | What it also needs at runtime |
|---|---|
| Google ADK | The generated project's own `requirements.txt`. |
| Claude Agent SDK | The generated project's own `requirements.txt`, and an `ANTHROPIC_API_KEY`. |
| Hermes Agent | The [Hermes runtime](https://github.com/NousResearch/hermes-agent), installed separately. A generated Hermes profile has no `requirements.txt`, and this install doesn't include Hermes. |

!!! note "Why `nuvel-cli` and not `nuvel`?"
    The PyPI distribution is named **`nuvel-cli`** because the bare name was too close to an existing package. The CLI command (`nuvel`) and every Python import (`from nuvel.cli import ...`) stay unchanged. Only the name you type after `pip install` differs.

## Install from source

```bash
git clone https://github.com/Folken2/nuvel.git
cd nuvel
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

Editable install is recommended if you plan to contribute to nuvel itself.

## Verify

```bash
nuvel --help
```

You should see subcommands `new`, `skills`, and `run`. If `nuvel` isn't found, your `pip install` location isn't on `$PATH` — use `python -m nuvel.cli --help` instead, or activate the right virtualenv.

Then run the health check:

```bash
export OPENROUTER_API_KEY=<your key>   # https://openrouter.ai/keys
nuvel doctor
```

It exits 0 when everything is in place. Without the key it exits non-zero and prints the command that fixes each problem; see [`nuvel doctor`](../reference/cli.md#nuvel-doctor).

## Next

Scaffold and run an agent in [Your first agent](first-agent.md).
