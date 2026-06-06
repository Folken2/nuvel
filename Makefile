# nuvel development commands

PLUGIN_FLAGS = \
	--extra_plugins nuvel.plugins.cost_guard \
	--extra_plugins nuvel.plugins.context_window \
	--extra_plugins nuvel.plugins.trace \
	--extra_plugins nuvel.plugins.context_filter \
	--extra_plugins nuvel.plugins.console_logger \
	--extra_plugins nuvel.plugins.tool_events \
	--extra_plugins nuvel.plugins.resilience \
	--extra_plugins nuvel.plugins.cache \
	--extra_plugins nuvel.plugins.self_healing \
	--extra_plugins nuvel.plugins.save_files \
	--extra_plugins nuvel.plugins.recordings \
	--extra_plugins nuvel.plugins.replay

.PHONY: install dev dev-ui run test cli skills

# Install the package in editable mode (exposes the `nuvel` binary)
install:
	pip install -e .

# ADK dev UI with all plugins loaded
dev-ui:
	adk web $(PLUGIN_FLAGS) .

# Development mode (DEV_MODE=true) via the nuvel CLI
dev:
	nuvel run --dev

# Production mode via the nuvel CLI
run:
	nuvel run

# Show available CLI subcommands
cli:
	nuvel --help

# List bundled skills
skills:
	nuvel skills list

# Run tests
test:
	python -m pytest tests/ -v
