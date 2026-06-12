/**
 * Backend API configuration.
 *
 * Values are injected at build time by webpack DefinePlugin.
 * Set BACKEND_URL and BACKEND_API_KEY as environment variables
 * before building (e.g. in Vercel dashboard or .env).
 */

import { getCurrentUser } from "./identity";

export const BACKEND_URL: string =
  (process.env.BACKEND_URL as string) || "http://localhost:8000";
export const BACKEND_API_KEY: string =
  (process.env.BACKEND_API_KEY as string) || "";

/** Standard headers sent with every JSON API request. */
export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (BACKEND_API_KEY) {
    headers["X-API-Key"] = BACKEND_API_KEY;
  }
  const user = getCurrentUser();
  if (user) {
    headers["X-User-Email"] = user.email;
    if (user.displayName) {
      headers["X-User-Display-Name"] = user.displayName;
    }
  }
  return headers;
}

/** Headers for non-JSON requests (e.g. file uploads). */
export function apiKeyHeader(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (BACKEND_API_KEY) headers["X-API-Key"] = BACKEND_API_KEY;
  const user = getCurrentUser();
  if (user) {
    headers["X-User-Email"] = user.email;
    if (user.displayName) headers["X-User-Display-Name"] = user.displayName;
  }
  return headers;
}

/** Stable per-installation session id, persisted in localStorage. */
export function getSessionId(): string {
  const key = "outlook-king.session_id";
  let id = localStorage.getItem(key);
  if (!id) {
    id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(key, id);
  }
  return id;
}

/* ── Cold-start / warmup helpers ── */

function isNetworkError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes("Failed to fetch") ||
    msg.includes("NetworkError") ||
    msg.includes("ERR_CONNECTION") ||
    msg.includes("ECONNREFUSED") ||
    msg.includes("Load failed")
  );
}

function isColdStartResponse(status: number): boolean {
  return status === 502 || status === 503 || status === 504;
}

function isTimeoutError(err: unknown): boolean {
  const name = (err as { name?: string } | null)?.name;
  return name === "AbortError" || name === "TimeoutError";
}

/**
 * Fetch with automatic retry for serverless cold starts.
 * Retries on network errors and 502/503/504 with exponential backoff.
 * When `timeoutMs` is set, each attempt gets its own fresh timeout and
 * timed-out attempts are retried too.
 */
export async function fetchWithRetry(
  input: RequestInfo,
  init?: RequestInit,
  {
    maxRetries = 3,
    baseDelayMs = 2000,
    timeoutMs,
  }: { maxRetries?: number; baseDelayMs?: number; timeoutMs?: number } = {},
): Promise<Response> {
  let lastError: unknown;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const attemptInit =
        timeoutMs != null ? { ...init, signal: AbortSignal.timeout(timeoutMs) } : init;
      const response = await fetch(input, attemptInit);
      if (isColdStartResponse(response.status) && attempt < maxRetries) {
        await delay(baseDelayMs * Math.pow(2, attempt));
        continue;
      }
      return response;
    } catch (err) {
      lastError = err;
      const retryable = isNetworkError(err) || (timeoutMs != null && isTimeoutError(err));
      if (retryable && attempt < maxRetries) {
        await delay(baseDelayMs * Math.pow(2, attempt));
        continue;
      }
      throw err;
    }
  }

  throw lastError;
}

/**
 * Parse a structured error payload (`{detail: {code, message}}`) from a
 * failed response. Returns a user-safe message, or "" when the body has
 * no recognizable detail.
 */
export async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (detail && typeof detail === "object" && typeof detail.message === "string") {
      return detail.message;
    }
    if (typeof detail === "string") return detail;
  } catch {
    /* non-JSON body */
  }
  return "";
}

/**
 * Check if the backend is ready. Returns true when /api/health responds 200.
 * Used on app mount to warm up the serverless backend.
 */
export async function waitForBackend(
  { maxAttempts = 10, baseDelayMs = 2000, onAttempt }: {
    maxAttempts?: number;
    baseDelayMs?: number;
    onAttempt?: (attempt: number) => void;
  } = {},
): Promise<boolean> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    onAttempt?.(attempt);
    try {
      const res = await fetch(`${BACKEND_URL}/api/health`, {
        method: "GET",
        signal: AbortSignal.timeout(5000),
      });
      if (res.ok) return true;
      if (res.status === 503) return true;
    } catch {
      // Network error — backend still booting
    }
    if (attempt < maxAttempts - 1) {
      await delay(Math.min(baseDelayMs * Math.pow(1.5, attempt), 10000));
    }
  }
  return false;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
