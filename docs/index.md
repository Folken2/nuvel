# nuvel

**Production-ready agents, your way.**

Skills, scaffolder, or meta-agent — build agents on **Google ADK**, the **Claude Agent SDK**, or **Anthropic Managed Agents** with the workflow you already have.

---

## What nuvel gives you

- **A scaffolder** — `nuvel new my-agent` drops a complete, deployable agent project into your filesystem. Pick a backend with `--framework`. No abstraction layer between you and the framework SDK.
- **Portable skills** — 13 progressive-disclosure skills shared across the three backends (web search, file tools, memory, Composio Tool Router, persona, more). Skills load only when relevant, so they don't bloat your context.
- **Optional bundles, all flag-driven** — every advanced capability is opt-in:
    - `--with-composio` — ~1000 third-party integrations via the Composio Tool Router.
    - `--persona` — a self-evolving SOUL.md / awakening pattern for agents meant to live for months.
    - `--with-slack` / `--with-telegram` / `--with-teams` — messaging-app gateways so a deployed agent is reachable from a chat platform.
- **A meta-agent** — `nuvel run` launches an interactive agent that scaffolds, configures, and ships other agents on your behalf.

## Where to go next

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Getting started**

    ---

    Install nuvel, scaffold your first agent, run it locally.

    [:octicons-arrow-right-24: Begin](getting-started/index.md)

-   :material-tools:{ .lg .middle } **How-to**

    ---

    Recipes for common tasks: pick a backend, add tools, wire messaging gateways, deploy.

    [:octicons-arrow-right-24: Recipes](how-to/index.md)

-   :material-book-open-variant:{ .lg .middle } **Reference**

    ---

    Every CLI flag, every environment variable, the repository layout.

    [:octicons-arrow-right-24: Reference](reference/index.md)

-   :material-account-multiple-plus:{ .lg .middle } **Contributing**

    ---

    How to add a new skill, a new channel, or improve the scaffolder.

    [:octicons-arrow-right-24: Contribute](contributing.md)

</div>

## Project status

nuvel is alpha (`0.1.x`). The CLI surface, scaffolded layout, and skill format are stable in spirit but may change in detail before `1.0`. Generated agents are real Python projects you own — they will keep working even if nuvel itself moves on.

## License

MIT. See [LICENSE](https://github.com/Folken2/nuvel/blob/main/LICENSE) for the full text.
