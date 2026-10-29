# Skills MCP server over HTTP, for container platforms (Railway, Fly, Cloud Run).
# Build context is this directory: nuvel-mcp has no runtime dependencies, so
# nothing outside it is needed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && rm -rf /app/src

# Mount a persistent volume at /data: the OAuth store (tokens, clients) and the
# feedback store (run traces, recipes) must survive redeploys. Runs as root
# because Railway volumes are mounted root-owned.
ENV NUVEL_MCP_OAUTH_STORE=/data/oauth.json \
    NUVEL_FEEDBACK_DIR=/data/feedback
RUN mkdir -p /data

EXPOSE 8080

# Shell form so $PORT (set by the platform) and the optional skills hub expand.
# NUVEL_MCP_PUBLIC_URL and the GitHub OAuth credentials come from the platform's
# variables; serve refuses to start on 0.0.0.0 without them.
CMD exec nuvel-mcp serve --transport http --allow-remote --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    ${NUVEL_MCP_SKILLS_DIR:+--skills-dir "$NUVEL_MCP_SKILLS_DIR"}
