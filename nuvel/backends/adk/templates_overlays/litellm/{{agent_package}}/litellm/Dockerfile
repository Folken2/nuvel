# Pinned by tag and digest, never a floating tag: litellm 1.107.0 was a
# compromised release, and main-latest would have pulled it. Raise only after
# checking the new release is clean (same rule as requirements.txt).
FROM ghcr.io/berriai/litellm:v1.106.2@sha256:7294b90f868c5c38c99b83021c7ba46f9b439e95f8e32964c925c7d2563a03f7

# Copy the LiteLLM proxy configuration
COPY config.yaml /app/config.yaml

# LiteLLM reads config from the current directory or via env var
WORKDIR /app
ENV LITELLM_CONFIG=/app/config.yaml

# Expose the LiteLLM proxy port
EXPOSE 4000

CMD ["--port", "4000", "--config", "/app/config.yaml"]