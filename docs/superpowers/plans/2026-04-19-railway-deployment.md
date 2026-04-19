# Railway Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every generated agent ships with Railway deployment config (`Dockerfile`, `railway.json`, `.dockerignore`) so `railway up` works out of the box.

**Architecture:** Add three static config files to `meta_agent/templates/`, update the README template with Railway deployment instructions, and add `.json` to scaffold's `TEXT_EXTENSIONS` for future-proofing. No runtime code changes.

**Tech Stack:** Docker, Railway (Dockerfile builder), FastAPI (existing `/health` endpoint)

**Spec:** `docs/superpowers/specs/2026-04-19-railway-deployment-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `meta_agent/templates/Dockerfile` | Create | Docker build recipe (python:3.12-slim + gcc) |
| `meta_agent/templates/railway.json` | Create | Railway build/deploy config with health check |
| `meta_agent/templates/.dockerignore` | Create | Exclude dev artifacts from Docker build context |
| `meta_agent/templates/README.md.tmpl` | Modify | Replace "Production Deployment" section with Railway instructions |
| `scaffold.py` | Modify | Add `.json` to TEXT_EXTENSIONS |

---

### Task 1: Add `.json` to scaffold TEXT_EXTENSIONS

**Files:**
- Modify: `scaffold.py:26-29`

- [ ] **Step 1: Add `.json` to TEXT_EXTENSIONS**

Replace lines 26-29 of `scaffold.py`:

```python
TEXT_EXTENSIONS = frozenset({
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml",
    ".cfg", ".ini", ".env", ".example", ".html", ".json",
})
```

- [ ] **Step 2: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('scaffold.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scaffold.py
git commit -m "feat: add .json to scaffold TEXT_EXTENSIONS"
```

---

### Task 2: Create Dockerfile

**Files:**
- Create: `meta_agent/templates/Dockerfile`

- [ ] **Step 1: Write `meta_agent/templates/Dockerfile`**

```dockerfile
# Use Python 3.12 slim image for smaller size
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway will set PORT env var)
EXPOSE 8000

# Run the application
# Railway automatically sets PORT environment variable
CMD ["python", "run_adk.py"]
```

- [ ] **Step 2: Verify the Dockerfile exists and is readable**

Run: `test -f meta_agent/templates/Dockerfile && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add meta_agent/templates/Dockerfile
git commit -m "feat(template): add Dockerfile for Railway deployment"
```

---

### Task 3: Create railway.json

**Files:**
- Create: `meta_agent/templates/railway.json`

- [ ] **Step 1: Write `meta_agent/templates/railway.json`**

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

- [ ] **Step 2: Verify the JSON is valid**

Run: `python -c "import json; json.load(open('meta_agent/templates/railway.json')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add meta_agent/templates/railway.json
git commit -m "feat(template): add railway.json with health check and restart policy"
```

---

### Task 4: Create .dockerignore

**Files:**
- Create: `meta_agent/templates/.dockerignore`

- [ ] **Step 1: Write `meta_agent/templates/.dockerignore`**

```
# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
.pytest_cache/

# Virtual environments
.venv/
venv/

# Secrets (provided via Railway env vars)
.env

# Runtime state (persisted via Railway volumes if needed)
traces/
memory/

# Development
.git/
.gitignore
tests/
docs/
*.md
!README.md

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 2: Verify the file exists**

Run: `test -f meta_agent/templates/.dockerignore && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add meta_agent/templates/.dockerignore
git commit -m "feat(template): add .dockerignore to trim Docker build context"
```

---

### Task 5: Update README template with Railway deployment section

**Files:**
- Modify: `meta_agent/templates/README.md.tmpl:144-146`

- [ ] **Step 1: Replace the "Production Deployment" section**

In `meta_agent/templates/README.md.tmpl`, find the existing section (starts with `## Production Deployment` on line 144) and replace it with:

```markdown
## Deploy to Railway

This agent ships with `Dockerfile`, `railway.json`, and `.dockerignore` — ready to deploy.

### One-time setup

```bash
# Install the Railway CLI
brew install railway  # or: npm install -g @railway/cli

# Log in
railway login
```

### Deploy

```bash
# From the agent directory:
railway init         # Create a Railway project (select "Empty Project")
railway link         # Link this directory to the project (if not already)
railway up           # Build + deploy
```

### Configure environment variables

Set these in the Railway dashboard (or via `railway variables --set KEY=VALUE`):

| Variable | Required | Notes |
|----------|----------|-------|
| `OPENROUTER_API_KEY` | ✅ | Your OpenRouter API key |
| `API_KEY` | Recommended | Enables Bearer-token auth on all endpoints |
| `DEV_MODE` | Optional | Defaults to `false`; set to `true` for in-memory sessions (no Postgres) |
| `GOOGLE_API_KEY` | Optional | Required when `STREAMING_ENABLED=true` |
| `STREAMING_ENABLED` | Optional | Set to `true` for voice/video agents |

### Optional: Postgres for persistent sessions

By default the agent runs with in-memory sessions (`DEV_MODE=true`). For persistent sessions:

1. In the Railway dashboard, add a Postgres plugin to your project
2. Copy the Postgres `DATABASE_URL` from the plugin's Variables tab
3. Set `SESSION_SERVICE_URI=<that URL>` on your agent service
4. Set `DEV_MODE=false`

### Health check

Railway polls `/health` (configured in `railway.json`) to verify the container is ready before routing traffic. The `/health` endpoint is public and requires no auth.
```

- [ ] **Step 2: Verify the file still has expected sections**

Run: `grep -c '^## ' meta_agent/templates/README.md.tmpl`
Expected: `6` or higher (the new section replaces one existing section, so count should not decrease)

- [ ] **Step 3: Verify no unresolved placeholders remain**

Run: `grep -E '(TBD|TODO|FIXME|XXX)' meta_agent/templates/README.md.tmpl; test $? -eq 1 && echo OK`
Expected: `OK` (grep exits non-zero when no matches)

- [ ] **Step 4: Commit**

```bash
git add meta_agent/templates/README.md.tmpl
git commit -m "docs(template): replace Production Deployment section with Railway instructions"
```

---

### Task 6: End-to-end scaffold verification

Verify that scaffolding a new agent produces a runnable Railway deployment.

- [ ] **Step 1: Scaffold a test agent**

```bash
python scaffold.py test-railway-agent --output-dir /tmp/test-railway --description "Railway deploy test"
```
Expected: `Agent scaffolded at: /tmp/test-railway/test-railway-agent`

- [ ] **Step 2: Verify all Railway config files exist at the top level**

```bash
ls -la /tmp/test-railway/test-railway-agent/Dockerfile \
       /tmp/test-railway/test-railway-agent/railway.json \
       /tmp/test-railway/test-railway-agent/.dockerignore
```
Expected: All three files listed with non-zero size

- [ ] **Step 3: Verify railway.json is valid JSON**

```bash
python -c "import json; c = json.load(open('/tmp/test-railway/test-railway-agent/railway.json')); assert c['deploy']['healthcheckPath'] == '/health'; assert c['deploy']['startCommand'] == 'python run_adk.py'; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Verify Dockerfile contents are intact (no placeholder corruption)**

```bash
grep -q 'python:3.12-slim' /tmp/test-railway/test-railway-agent/Dockerfile && \
grep -q 'CMD \["python", "run_adk.py"\]' /tmp/test-railway/test-railway-agent/Dockerfile && \
echo OK
```
Expected: `OK`

- [ ] **Step 5: Verify .dockerignore excludes key paths**

```bash
grep -q '^.venv/' /tmp/test-railway/test-railway-agent/.dockerignore && \
grep -q '^.env$' /tmp/test-railway/test-railway-agent/.dockerignore && \
grep -q '^traces/' /tmp/test-railway/test-railway-agent/.dockerignore && \
echo OK
```
Expected: `OK`

- [ ] **Step 6: Verify README contains the Railway section**

```bash
grep -q '^## Deploy to Railway' /tmp/test-railway/test-railway-agent/README.md && \
grep -q 'railway up' /tmp/test-railway/test-railway-agent/README.md && \
echo OK
```
Expected: `OK`

- [ ] **Step 7: Verify README has no unresolved {{placeholder}} tokens**

```bash
grep -c '{{' /tmp/test-railway/test-railway-agent/README.md
```
Expected: `0`

- [ ] **Step 8: Clean up**

```bash
rm -rf /tmp/test-railway
```

No commit needed — this was a verification-only task.
