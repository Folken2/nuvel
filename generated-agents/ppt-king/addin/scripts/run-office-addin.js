#!/usr/bin/env node
/* eslint-disable no-undef */

const { spawnSync } = require("child_process");
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

const manifestPath = format === "json" ? "manifest.json" : "manifest.xml";
const bin = subcommand === "validate" ? "office-addin-manifest" : "office-addin-debugging";
const args = subcommand === "validate" ? ["validate", manifestPath] : [subcommand, manifestPath];

console.log(`[ppt-king/addin] OFFICE_MANIFEST=${format} -> ${bin} ${args.join(" ")}`);

const result = spawnSync(bin, args, {
  stdio: "inherit",
  shell: process.platform === "win32",
  cwd: path.resolve(__dirname, ".."),
});

process.exit(result.status ?? 1);
