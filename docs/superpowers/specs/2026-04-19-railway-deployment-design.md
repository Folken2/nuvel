# Railway Deployment for Generated Agents — Design Spec

> **Date:** 2026-04-19
> **Status:** Approved
> **Goal:** Every generated agent ships with Railway deployment config so `railway up` works out of the box.

---

## Context

Generated agents currently lack deployment config. The standalone `data-analysis-agent/` has a hand-rolled `Dockerfile` but nothing in the template, so every new agent needs manual deployment setup. This spec adds Railway config to the scaffold template.

**Non-goals:** No deploy script, no Postgres auto-setup, no env var sync. Scope is strictly the config files.

---

## Design

### 1. New Template File: `meta_agent/templates/Dockerfile`

Static file, no placeholders. Identical to `data-analysis-agent/Dockerfile`:

- Base: `python:3.12-slim`
- Installs `gcc` via apt for C-extension builds
- Copies `requirements.txt` first for Docker layer caching
- `EXPOSE 8000`, `CMD ["python", "run_adk.py"]`

Railway sets `PORT` automatically; `run_adk.py` reads it via `os.getenv("PORT", "8000")`.

### 2. New Template File: `meta_agent/templates/railway.json`

Static file, no placeholders:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "startCommand": "python run_adk.py",
    "healthcheckPath": "/health",
    "healthcheckTimeout": 100,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}
```

- `healthcheckPath: /health` matches the existing endpoint in `run_adk.py` (public, no auth required)
- `restartPolicyMaxRetries: 3` recovers from transient LLM provider failures without infinite loops

### 3. New Template File: `meta_agent/templates/.dockerignore`

Excludes development-only artifacts from the Docker build context:

- `.venv/`, `__pycache__/`, `*.pyc`
- `.env` (secrets come from Railway env vars)
- `traces/`, `memory/` (runtime state, persisted via volume)
- `.git/`, `tests/`, `docs/`, `.DS_Store`

### 4. Modified: `meta_agent/templates/README.md.tmpl`

Replace the existing "Production Deployment" section (line 144-146) with a Railway-specific section covering:

1. Install Railway CLI (`brew install railway`)
2. `railway login`
3. `railway init` (creates a project)
4. Set env vars via dashboard or `railway variables --set KEY=VALUE`:
   - Required: `OPENROUTER_API_KEY`
   - Recommended: `API_KEY` (auth)
   - Optional: `GOOGLE_API_KEY` (streaming), `SESSION_SERVICE_URI` (Postgres)
5. `railway up` to deploy
6. By default runs with `DEV_MODE=true` (in-memory sessions). For persistent sessions, add a Postgres plugin in Railway and set `SESSION_SERVICE_URI` from the Postgres `DATABASE_URL`, then set `DEV_MODE=false`.

### 5. Modified: `scaffold.py`

Add `.json` and `.dockerignore` to `TEXT_EXTENSIONS` and handle extensionless `Dockerfile`. Two specific changes:

- Add `.json` to the `TEXT_EXTENSIONS` frozenset (future-proofing in case placeholders are added to JSON files)
- Ensure `Dockerfile` (no extension) is copied correctly. The current scaffold falls back to `shutil.copy2` for non-text files, which is fine for a static Dockerfile — no change needed if we leave it as binary-copied.

**Verification during scaffold:** after `scaffold_agent("test-deploy")` runs, the generated directory must contain `Dockerfile`, `railway.json`, and `.dockerignore` at the top level.

---

## What Does NOT Change

- `run_adk.py` — no changes; Railway sets `PORT`, `DEV_MODE`, and other env vars at runtime
- Plugin chain, agent wiring, skills — untouched
- No new env vars introduced
- Existing agents (data-analysis-agent, ai-news-weekly-digest) — not retroactively migrated

---

## Summary

| File | Action |
|------|--------|
| `templates/Dockerfile` | Create (static, ~20 lines) |
| `templates/railway.json` | Create (static, ~15 lines) |
| `templates/.dockerignore` | Create (static, ~10 lines) |
| `templates/README.md.tmpl` | Edit — Railway deployment section |
| `scaffold.py` | Edit — add `.json` to TEXT_EXTENSIONS |

**Total: 3 new files, 2 edits.**
