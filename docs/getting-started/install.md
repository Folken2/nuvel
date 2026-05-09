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

This installs the `nuvel` CLI plus everything needed to scaffold and run agents on any of the supported backends.

!!! note "Why `nuvel-cli` and not `nuvel`?"
    The PyPI distribution is named **`nuvel-cli`** because the bare name was too close to an existing package. The CLI command (`nuvel`) and every Python import (`from nuvel.cli import ...`) stay unchanged. Only the name you type after `pip install` differs.

## Install from source

```bash
git clone https://github.com/Folken2/nuvel.git
cd nuvel
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Editable install is recommended if you plan to contribute to nuvel itself.

## Verify

```bash
nuvel --help
```

You should see subcommands `new`, `skills`, and `run`. If `nuvel` isn't found, your `pip install` location isn't on `$PATH` — use `python -m nuvel.cli --help` instead, or activate the right virtualenv.

## Next

Scaffold and run an agent in [Your first agent](first-agent.md).
