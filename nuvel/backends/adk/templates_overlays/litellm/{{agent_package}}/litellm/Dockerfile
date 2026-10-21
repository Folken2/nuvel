FROM ghcr.io/berriai/litellm:main-latest

# Copy the LiteLLM proxy configuration
COPY config.yaml /app/config.yaml

# LiteLLM reads config from the current directory or via env var
WORKDIR /app
ENV LITELLM_CONFIG=/app/config.yaml

# Expose the LiteLLM proxy port
EXPOSE 4000

CMD ["--port", "4000", "--config", "/app/config.yaml"]