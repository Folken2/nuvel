# Deploy to Railway

Every nuvel-scaffolded ADK agent ships with `Dockerfile` + `railway.json` ready for Railway. Path of least resistance.

## What's in the box

After `nuvel agent create my-agent --framework adk`:

- **`Dockerfile`** — `python:3.12-slim`, installs `gcc` for C extensions, copies `requirements.txt` first for layer caching, runs `python run_adk.py`.
- **`railway.json`** — points at the Dockerfile, sets `/health` as the healthcheck, on-failure restart with up to 3 retries.

## Deploy

1. **Push your agent to GitHub** (a repo for the agent itself, separate from nuvel).
2. **Create a Railway project** → New Project → Deploy from GitHub repo. Pick your agent's repo.
3. **Set environment variables** in the Railway dashboard (Variables tab). At minimum:

   ```
   OPENROUTER_API_KEY=sk-or-...
   API_KEY=<long random string>             # Bearer token for /run, /run_sse, etc.
   SESSION_SERVICE_URI=postgresql://...     # Required in production (DEV_MODE=false)
   ```

   For each enabled feature, add the relevant vars (Composio, channels, etc.).

4. **Add a Postgres plugin** via Railway's marketplace, then copy the `DATABASE_URL` it provisions into `SESSION_SERVICE_URI`.

5. **Deploy.** Railway picks up `railway.json` and builds from the Dockerfile.

## Healthcheck

The generated `run_adk.py` exposes `GET /health` returning `{"status":"healthy"}`. Railway uses this for the `/health` probe configured in `railway.json`. The endpoint is exempt from `APIKeyMiddleware`, so Railway can hit it without credentials.

## Custom domain

Once deployed, Railway gives you a `*.up.railway.app` hostname. To use a custom domain:

1. **Settings → Networking → Custom domain** → add yours.
2. **Update DNS** (CNAME record to the Railway-provided target).
3. **Wait** for the cert to provision (Railway handles Let's Encrypt automatically).

## Two-process deployments (Teams)

If you enabled `--with-teams`, you have a sidecar process that needs its own deployment. On Railway:

- **Service A: agent server** (the one auto-detected). Keeps running `python run_adk.py`.
- **Service B: Teams bridge.** New service in the same project. In its **Settings → Deploy**, override the start command to `python -m my_agent.gateways.teams_bridge`. Set `AGENT_BASE_URL` to Service A's internal URL (`http://${{agent.RAILWAY_PRIVATE_DOMAIN}}:8000`). Both services share the same Postgres.

## Cron / scheduled jobs on Railway

If you enabled the cron scheduler (`NUVEL_CRON_ENABLED=1`), three Railway specifics matter.

### 1. Persist jobs across deploys with a Volume

By default the scheduler stores jobs at `~/.nuvel/cron/jobs.json` and outputs at `~/.nuvel/cron/output/`. Railway's container filesystem is **wiped on every deploy** — without persistence, every job you create is lost the next time you push code.

Fix: attach a Railway **Volume** to the agent service.

1. **Service → Settings → Volumes → New Volume.** Mount path `/data`, size 1 GB is plenty.
2. Set `NUVEL_CRON_DIR=/data/cron` in the service's variables.
3. Redeploy. From now on, jobs and outputs live on the volume and survive deploys.

Cost is ~$0.25/GB/month — negligible.

### 2. Stay on a non-sleeping plan

The scheduler is an in-process asyncio task. If Railway sleeps the container (Hobby tier, idle services), the scheduler stops ticking and cron silently halts. Either stay on a paid plan that keeps the container warm, or set up an external uptime monitor (UptimeRobot etc.) hitting `/health` every minute to keep the container active.

### 3. Single replica only (for now)

Each replica runs its own scheduler — scaling beyond 1 instance will fire every cron job N times. The MVP doesn't have a distributed lock yet, so keep `replicas=1` on the agent service if cron is enabled. Postgres-backed storage with advisory locks is a planned follow-up.

### Railway-native cron as an alternative

If you only need a couple of fixed scheduled tasks and don't want to think about persistence, Railway offers a separate **Cron Jobs** service type — point it at your agent's `POST /cron/jobs/{id}/run` endpoint with the right auth header and let Railway own the clock. Trade-off: you lose `/cron add` from chat.

## Other platforms

The scaffold is platform-agnostic — `Dockerfile` + a port via `$PORT` env var works on Fly.io, Render, Cloud Run, ECS, anything that takes a container.

`railway.json` is harmless on other platforms (it's ignored). Remove it if it bothers you.

## Logs

```bash
railway logs --tail
```

The agent emits structured logs (configurable via `LOG_FORMAT=json|text`). For production aggregation, set `LOG_FORMAT=json` and route Railway logs to your aggregator of choice.
