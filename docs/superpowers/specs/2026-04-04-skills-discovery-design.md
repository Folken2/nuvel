# Skills Discovery & Installation Design Spec

> Enable the meta-agent to search, download, adapt, and install community skills from skills.sh into generated ADK agents.

## Core Principle

Community skills follow the agentskills.io spec (designed for Claude Code, Cursor, Gemini CLI). ADK's `load_skill_from_dir` has stricter validation. The meta-agent bridges this gap with an automatic adaptation pipeline.

## Tools

Three new tools in `meta_agent/tools/skills_tools.py`:

### search_skills(query)
- Calls `GET https://skills.sh/api/search?q=<query>` — returns structured JSON (no CLI needed)
- **Security gate:** Filters results to >= 1,000 installs only
- Returns a list of matching skills with: name, package identifier (`owner/repo@skill`), install count, URL
- The meta-agent uses this to discover relevant skills before writing from scratch

### install_skill(package, agent_name)
- Downloads the skill via `npx skills add <package>` to a temp directory
- Verifies the skill has >= 1,000 installs (rejects below threshold)
- Runs the ADK adaptation pipeline (see below)
- Validates with ADK's `load_skill_from_dir`
- Copies the adapted skill to the generated agent's `skills/` directory
- Returns success/failure with any warnings from adaptation

### read_skill_context(package)
- Downloads to a temp directory
- Verifies >= 1,000 installs
- Reads the SKILL.md content + any references/ files
- Returns the content as a string for the meta-agent to use as inspiration
- Does NOT install — just provides context for the LLM to write a better custom skill
- Temp directory cleaned up after reading

## ADK Adaptation Pipeline

When `install_skill` downloads a community skill, it runs through this pipeline:

### Step 1: Parse
Read SKILL.md, split YAML frontmatter from markdown body.

### Step 2: Strip invalid frontmatter keys
ADK only allows these frontmatter keys:
- `name` (required)
- `description` (required)
- `license` (optional)
- `allowed-tools` / `allowed_tools` (optional)
- `metadata` (optional)
- `compatibility` (optional)

Drop everything else: `trigger`, `version`, `author`, `tags`, `category`, etc.

### Step 3: Fix naming
- If directory name doesn't match frontmatter `name`, rename directory
- If name isn't valid kebab-case (uppercase, underscores, special chars), normalize it
- Kebab-case rules: `[a-z0-9]+(-[a-z0-9]+)*`, max 64 chars

### Step 4: Validate description
- Ensure non-empty
- Truncate to 1024 chars if longer (with ellipsis)

### Step 5: Clean resources
- Keep `references/` directory (`.md` files) — maps to ADK L3 resources
- Keep `assets/` as-is
- Drop `scripts/` — ADK doesn't support script execution
- Skip `__pycache__` and binary files

### Step 6: Validate with ADK
- Call `load_skill_from_dir` on the adapted skill directory
- If it loads successfully, the skill is ADK-compatible
- If it fails, report the specific validation error and don't install

### Step 7: Copy
- Move from temp dir to `<agent_dir>/<agent_package>/skills/<skill-name>/`

The pipeline is implemented as a pure function: `adapt_skill_for_adk(source_dir) -> (adapted_dir, warnings_list)`, testable in isolation.

## Security

### Download threshold
- Only skills with >= 1,000 installs are allowed
- This filters out unvetted skills that could contain malicious instructions, prompt injection, or exfiltration patterns
- The threshold is enforced in all three tools (search filters display, install/read reject below threshold)

### Content is instruction-only
- Skills contain markdown instructions and references, not executable code
- ADK's `scripts/` execution is not supported and we drop that directory
- The risk surface is limited to prompt content that could influence LLM behavior

## Integration

### New file
- `meta_agent/tools/skills_tools.py` — All three tools + adaptation pipeline

### Modified files
- `meta_agent/tools/__init__.py` — Add three new tools to `get_tools()`
- `meta_agent/prompt/instructions.py` — Add skill discovery section to system prompt

### No changes to
- scaffold.py, templates, plugins, config, agent.py, or any other existing files

### Dependencies
- `npx skills` CLI must be available (Node.js runtime) — used by `install_skill` and `read_skill_context` for downloading
- `search_skills` uses the skills.sh HTTP API directly (no CLI needed)
- No new Python packages — we use `urllib` for the API and shell out to the CLI for downloads

## System Prompt Addition

Added to the Generate phase of the workflow:

```
## 4b. Discover Existing Skills (optional)
Before writing skills from scratch, search for community skills:
- Call `search_skills("keyword")` to find relevant skills (1K+ downloads only)
- Call `read_skill_context("owner/repo@skill-name")` to read a skill's content as inspiration
- Call `install_skill("owner/repo@skill-name", agent_name)` to install directly (auto-adapted for ADK)

Prefer installing proven community skills over writing from scratch when a good match exists.
When no good match exists, use community skills as context to write better custom skills.
```

## Success Criteria

1. `search_skills("kubernetes")` returns results filtered to 1K+ installs
2. `install_skill("owner/repo@skill", "my-agent")` downloads, adapts, validates, and installs an ADK-compatible skill
3. `read_skill_context("owner/repo@skill")` returns SKILL.md content for LLM consumption
4. Skills with invalid ADK frontmatter are automatically adapted (extra keys stripped, names fixed)
5. Skills that can't be adapted are rejected with clear error messages
6. `load_skill_from_dir` succeeds on every installed skill
7. Skills below 1K installs are rejected in all three tools
