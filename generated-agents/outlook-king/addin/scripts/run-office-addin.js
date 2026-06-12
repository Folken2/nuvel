#!/usr/bin/env node
/* eslint-disable no-undef */

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");

const subcommand = process.argv[2];
if (!subcommand) {
  console.error("Usage: run-office-addin.js <start|stop|validate>");
  process.exit(1);
}

const format = (process.env.OFFICE_MANIFEST || "xml").toLowerCase();
if (format !== "xml" && format !== "json") {
  console.error(`OFFICE_MANIFEST must be "xml" or "json" (got "${process.env.OFFICE_MANIFEST}")`);
  process.exit(1);
}

const addinRoot = path.resolve(__dirname, "..");
const manifestPath = format === "json" ? "manifest.json" : "manifest.xml";
const devServerPort = process.env.npm_package_config_dev_server_port || 3000;

// office-addin-debugging cannot sideload into Outlook on macOS (Windows-only,
// see office-addin-dev-settings isSideloadingSupportedForDesktopHost). On Mac
// the manifest is sideloaded once by hand (https://aka.ms/olksideload) and
// persists; day-to-day we only need the dev server running and Outlook open.
const isMac = process.platform === "darwin";

function log(message) {
  console.log(`[outlook-king/addin] ${message}`);
}

function runSync(bin, args) {
  log(`${bin} ${args.join(" ")}`);
  const result = spawnSync(bin, args, {
    stdio: "inherit",
    shell: process.platform === "win32",
    cwd: addinRoot,
  });
  process.exit(result.status ?? 1);
}

function devCertCa() {
  // Node ignores the macOS keychain, so trust the office-addin dev CA directly.
  const caPath = path.join(os.homedir(), ".office-addin-dev-certs", "ca.crt");
  return fs.existsSync(caPath) ? fs.readFileSync(caPath) : undefined;
}

function probeDevServer(ca) {
  return new Promise((resolve) => {
    const req = https.get(
      { host: "localhost", port: devServerPort, path: "/taskpane.html", ca },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      }
    );
    req.on("error", () => resolve(false));
    req.setTimeout(2000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForDevServer(ca, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    if (await probeDevServer(ca)) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`dev server not responding on port ${devServerPort}`);
}

function openOutlook() {
  log("opening Outlook");
  spawnSync("open", ["-a", "Microsoft Outlook"], { stdio: "inherit" });
}

async function startMac() {
  log(`macOS: Outlook auto-sideload is unsupported — assuming ${manifestPath} was sideloaded once via https://aka.ms/olksideload`);
  const ca = devCertCa();

  if (await probeDevServer(ca)) {
    log(`dev server already running on https://localhost:${devServerPort} — reusing it`);
    openOutlook();
    return;
  }

  log(`starting dev server on https://localhost:${devServerPort} ...`);
  const devServer = spawn("npx", ["webpack", "serve", "--mode", "development"], {
    stdio: "inherit",
    cwd: addinRoot,
  });

  devServer.on("exit", (code) => process.exit(code ?? 0));
  const stopDevServer = () => devServer.kill("SIGTERM");
  process.on("SIGINT", stopDevServer);
  process.on("SIGTERM", stopDevServer);

  try {
    await waitForDevServer(ca);
    log("dev server is up");
    openOutlook();
    log("press Ctrl+C to stop the dev server");
  } catch (err) {
    console.error(`[outlook-king/addin] ${err.message}`);
    stopDevServer();
  }
}

function stopMac() {
  const result = spawnSync("lsof", ["-ti", `:${devServerPort}`], { encoding: "utf8" });
  const pids = (result.stdout || "").split("\n").filter(Boolean);
  if (pids.length === 0) {
    log(`no dev server running on port ${devServerPort}`);
    return;
  }
  for (const pid of pids) {
    process.kill(Number(pid), "SIGTERM");
  }
  log(`stopped dev server (pid ${pids.join(", ")})`);
}

if (subcommand === "validate") {
  runSync("office-addin-manifest", ["validate", manifestPath]);
} else if (subcommand === "start") {
  if (isMac) {
    startMac();
  } else {
    runSync("office-addin-debugging", ["start", manifestPath]);
  }
} else if (subcommand === "stop") {
  if (isMac) {
    stopMac();
  } else {
    runSync("office-addin-debugging", ["stop", manifestPath]);
  }
} else {
  console.error(`Unknown subcommand: ${subcommand}`);
  process.exit(1);
}
