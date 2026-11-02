# Skills MCP server over HTTP, for container platforms (Railway, Fly, Cloud Run).
# Build context is this directory: nuvel-mcp has no runtime dependencies, so
# nothing outside it is needed. The image adds psycopg for the Postgres store.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . "psycopg[binary,pool]>=3.2,<4" && rm -rf /app/src

# Storage: set NUVEL_MCP_DATABASE_URL (e.g. Neon's pooled connection string) and
# OAuth, traces, recipes and usage all live in Postgres; nothing on disk matters.
# Without it the server falls back to files under /data, which then needs a
# persistent volume. The server runs as the unprivileged system user "nuvel":
# /data is created owned by it, so the fallback works in the image, but a
# volume mounted there is owned by whoever the platform makes it (Railway:
# root) and must be made writable for uid 10001 first.
ENV NUVEL_MCP_OAUTH_STORE=/data/oauth.json \
    NUVEL_FEEDBACK_DIR=/data/feedback
RUN groupadd --system --gid 10001 nuvel \
    && useradd --system --uid 10001 --gid nuvel --home-dir /data \
       --no-create-home --shell /usr/sbin/nologin nuvel \
    && mkdir -p /data/feedback \
    && chown -R nuvel:nuvel /data \
    && chmod 0700 /data

# X-Forwarded-For is not trusted by default: a plain `docker run -p` exposes
# the server with no proxy in front, where callers could forge it. Behind
# Railway (or any proxy that appends the caller's address), set
# NUVEL_MCP_TRUST_FORWARDED_FOR=1 in the platform's variables.

USER nuvel

EXPOSE 8080

# Shell form so $PORT (set by the platform) and the optional skills hub expand.
# NUVEL_MCP_PUBLIC_URL and either NUVEL_OAUTH_LOGIN=none (URL keys) or the GitHub
# OAuth credentials come from the platform's variables; serve refuses to start
# on 0.0.0.0 without them.
CMD exec nuvel-mcp serve --transport http --allow-remote --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    ${NUVEL_MCP_SKILLS_DIR:+--skills-dir "$NUVEL_MCP_SKILLS_DIR"}
