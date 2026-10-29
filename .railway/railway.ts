import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

// The Skills MCP server (packages/nuvel-mcp) over HTTP. Variable values are
// managed in Railway (preserve()), never committed here.
export default defineRailway(() => {
  // File-store fallback (OAuth, run traces, recipes). Once NUVEL_MCP_DATABASE_URL
  // is set and `nuvel-mcp db import` has run, state lives in Postgres and the
  // volume can be removed.
  const nuvelVolume = volume("nuvel-volume", { region: "europe-west4-drams3a", sizeMB: 5000 });
  const nuvel = service("nuvel", {
    source: github("Folken2/nuvel", { branch: "main", rootDirectory: "/packages/nuvel-mcp" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    replicas: { "europe-west4-drams3a": 1 },
    deploy: { restartPolicyMaxRetries: 3 },
    volumeMounts: { "/data": nuvelVolume },
    env: {
      NUVEL_MCP_DATABASE_URL: preserve(),
      NUVEL_MCP_PUBLIC_URL: preserve(),
      NUVEL_OAUTH_ALLOWED_USERS: preserve(),
      NUVEL_OAUTH_LOGIN: preserve(),
      PORT: preserve(),
    },
  });

  return project("nuvel-mcp-claude-plugin", {
    resources: [nuvel, nuvelVolume],
  });
});
