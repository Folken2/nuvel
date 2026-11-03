# nuvel

**Production-ready agents, your way.**

Skills, scaffolder, or meta-agent — build agents on **Google ADK**, **Hermes Agent**, or the **Claude Agent SDK** with the workflow you already have.

---

## What nuvel gives you

- **A scaffolder** — `nuvel agent create my-agent` drops a complete, deployable ADK project into your filesystem. Pick another backend with `--framework`: the Claude Agent SDK also gets a deployable project with a Dockerfile, while Hermes gets a four-file profile that the Hermes runtime loads. No abstraction layer between you and the framework SDK.
- **Knowledge skills per backend** — progressive-disclosure skills in the portable Anthropic format: [15 for ADK, 6 for the Claude Agent SDK, 1 for Hermes](how-to/add-skills.md). Skills load only when relevant, so they don't bloat your context.
- **Optional bundles, all flag-driven** — every advanced capability is opt-in ([support per backend](how-to/pick-a-backend.md#nuvel-feature-support)):
    - `--with-composio` *(adk only)* — ~1000 third-party integrations via the Composio Tool Router.
    - `--persona` *(adk only)* — a self-evolving SOUL.md / awakening pattern for agents meant to live for months.
    - `--with-slack` / `--with-teams` *(adk only)*, `--with-telegram` *(adk, hermes)* — messaging-app gateways so a deployed agent is reachable from a chat platform.
- **A meta-agent** — `nuvel run` launches an interactive agent that scaffolds and configures ADK agents on your behalf.

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

nuvel is alpha (`0.1.x`). The CLI surface, scaffolded layout, and skill format are stable in spirit but may change in detail before `1.0`. Generated agents are yours — real Python projects on ADK and the Claude Agent SDK, a Hermes profile on Hermes — and they will keep working even if nuvel itself moves on.

## License

MIT. See [LICENSE](https://github.com/Folken2/nuvel/blob/main/LICENSE) for the full text.
