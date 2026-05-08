# VHS tape script for the nuvel README demo GIF.
#
# Renders a ~25s clip showing nuvel scaffolding agents across three
# frameworks. Drops to docs/assets/demo.gif for the README hero.
#
# Run:
#   brew install vhs           # one-time
#   cd /path/to/nuvel
#   vhs docs/assets/demo.tape
#
# The script needs `nuvel` on PATH — activate the project's venv first
# (e.g. `source .venv/bin/activate`) so `nuvel` resolves.

Output docs/assets/demo.gif

# Visual identity — warm cream theme to match the wordmark direction.
Set Theme "Catppuccin Latte"
Set FontFamily "JetBrains Mono"
Set FontSize 16
Set Width 1200
Set Height 720
Set Padding 30
Set TypingSpeed 40ms
Set PlaybackSpeed 1.0

# Stage: clean the demo directory before recording starts.
Hide
Type "rm -rf /tmp/demo && mkdir -p /tmp/demo && cd /tmp/demo"
Enter
Type "clear"
Enter
Show

# 1. ADK agent (default framework)
Type "nuvel new k8s-monitor --description 'checks pod health and alerts on anomalies'"
Sleep 600ms
Enter
Sleep 2500ms

# 2. Claude Agent SDK
Type "nuvel new code-reviewer --framework claude-agent-sdk --description 'reviews PRs'"
Sleep 600ms
Enter
Sleep 2500ms

# 3. Anthropic Managed Agents
Type "nuvel new research-bot --framework anthropic-managed-agents --description 'long-form research'"
Sleep 600ms
Enter
Sleep 2500ms

# 4. Show the bundled knowledge skills for one of them
Type "nuvel skills list --framework claude-agent-sdk"
Sleep 400ms
Enter
Sleep 3500ms

# Final beat — let the viewer take it in.
Sleep 1500ms
