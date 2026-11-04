import { defineRailway, github, preserve, project, service } from "railway/iac";

// The Skills MCP server (the Folken2/nuvel-mcp repo) over HTTP. Variable values are
// managed in Railway (preserve()), never committed here. All state (OAuth, run
// traces, recipes, usage) lives in Postgres via NUVEL_MCP_DATABASE_URL.
export default defineRailway(() => {
  const nuvel = service("nuvel", {
    source: github("Folken2/nuvel-mcp", { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    replicas: { "europe-west4-drams3a": 1 },
    deploy: { restartPolicyMaxRetries: 3 },
    env: {
      NUVEL_MCP_DATABASE_URL: preserve(),
      NUVEL_MCP_PUBLIC_URL: preserve(),
      NUVEL_MCP_TRUST_FORWARDED_FOR: preserve(),
      NUVEL_OAUTH_ALLOWED_USERS: preserve(),
      NUVEL_OAUTH_LOGIN: preserve(),
      PORT: preserve(),
    },
  });

  return project("nuvel-mcp-claude-plugin", {
    resources: [nuvel],
  });
});
