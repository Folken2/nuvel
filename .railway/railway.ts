import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

// The Skills MCP server (packages/nuvel-mcp) over HTTP. Variable values are
// managed in Railway (preserve()), never committed here.
export default defineRailway(() => {
  // OAuth store, run traces and recipes: they must survive redeploys.
  const nuvelVolume = volume("nuvel-volume", { region: "europe-west4-drams3a", sizeMB: 5000 });
  const nuvel = service("nuvel", {
    source: github("Folken2/nuvel", { branch: "main", rootDirectory: "/packages/nuvel-mcp" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    replicas: { "europe-west4-drams3a": 1 },
    deploy: { restartPolicyMaxRetries: 3 },
    volumeMounts: { "/data": nuvelVolume },
    env: {
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
