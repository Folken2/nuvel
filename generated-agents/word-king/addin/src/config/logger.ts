/**
 * Remote logger — sends frontend logs to the backend so they appear in
 * server logs.
 *
 * Usage:
 *   import logger from "../config/logger";
 *   logger.info("context loaded", { paragraphs: 12 });
 *   logger.warn("snapshot failed", { error: err.message });
 *   logger.error("getContext failed", { error: err.message });
 *
 * Logs are batched and flushed every 2 seconds (or when the buffer hits 20
 * entries). Falls back to console silently if the backend is unreachable
 * or has no /api/logs endpoint.
 */

import { BACKEND_URL, apiHeaders } from "./api";

type LogLevel = "debug" | "info" | "warn" | "error";

interface LogEntry {
  level: LogLevel;
  message: string;
  data?: Record<string, unknown>;
  timestamp: string;
  source: string;
}

const BUFFER_MAX = 20;
const FLUSH_INTERVAL_MS = 2000;

let buffer: LogEntry[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    flush();
  }, FLUSH_INTERVAL_MS);
}

async function flush() {
  if (buffer.length === 0) return;
  const batch = buffer;
  buffer = [];
  try {
    await fetch(`${BACKEND_URL}/api/logs`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ entries: batch }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    // Silent failure — don't break the app for logging
  }
}

function log(level: LogLevel, message: string, data?: Record<string, unknown>, source = "frontend") {
  const entry: LogEntry = {
    level,
    message,
    data,
    timestamp: new Date().toISOString(),
    source,
  };

  const consoleFn = level === "error" ? console.error : level === "warn" ? console.warn : console.log;
  consoleFn(`[${level.toUpperCase()}] ${message}`, data ?? "");

  buffer.push(entry);
  if (buffer.length >= BUFFER_MAX) {
    flush();
  } else {
    scheduleFlush();
  }
}

const logger = {
  debug: (msg: string, data?: Record<string, unknown>) => log("debug", msg, data),
  info: (msg: string, data?: Record<string, unknown>) => log("info", msg, data),
  warn: (msg: string, data?: Record<string, unknown>) => log("warn", msg, data),
  error: (msg: string, data?: Record<string, unknown>) => log("error", msg, data),
  flush,
};

export default logger;
