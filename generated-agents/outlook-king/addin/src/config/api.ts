/**
 * Backend wiring.
 *
 * BACKEND_URL and BACKEND_API_KEY are injected at build time by
 * webpack's DefinePlugin. Defaults work for the local dev server.
 */

export const BACKEND_URL: string = (process.env.BACKEND_URL as string) || "http://localhost:8000";
export const BACKEND_API_KEY: string = (process.env.BACKEND_API_KEY as string) || "";

export function apiHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json", ...extra };
  if (BACKEND_API_KEY) h["X-API-Key"] = BACKEND_API_KEY;
  return h;
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
