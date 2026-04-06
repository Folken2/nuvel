# Meta-Agent development commands

PLUGIN_FLAGS = \
	--extra_plugins meta_agent.plugins.trace \
	--extra_plugins meta_agent.plugins.context_filter \
	--extra_plugins meta_agent.plugins.console_logger \
	--extra_plugins meta_agent.plugins.tool_events \
	--extra_plugins meta_agent.plugins.resilience \
	--extra_plugins meta_agent.plugins.cache \
	--extra_plugins meta_agent.plugins.self_healing \
	--extra_plugins meta_agent.plugins.save_files

.PHONY: dev dev-ui run test

# ADK dev UI with all plugins loaded
dev-ui:
	adk web $(PLUGIN_FLAGS) .

# Custom entrypoint (production-like, no UI)
dev:
	DEV_MODE=true python run_adk.py

# Production mode
run:
	python run_adk.py

# Run tests
test:
	python -m pytest tests/ -v
