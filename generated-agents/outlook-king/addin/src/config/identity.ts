/* global Office */

/**
 * Read the current Outlook user's identity from Office.js.
 *
 * Falls back to localStorage cache when Office.context is unavailable
 * (commands.html context, early init). Returns null when no identity
 * is available — callers should treat that as a fatal error since the
 * backend requires X-User-Email.
 */
export interface UserIdentity {
  email: string;
  displayName: string;
}

const CACHE_KEY = "outlook-king.user_identity";

export function getCurrentUser(): UserIdentity | null {
  try {
    const profile = (Office as any)?.context?.mailbox?.userProfile;
    if (profile?.emailAddress) {
      const identity: UserIdentity = {
        email: profile.emailAddress,
        displayName: profile.displayName || "",
      };
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(identity));
      } catch {
        // Storage unavailable — non-fatal.
      }
      return identity;
    }
  } catch {
    // Office.context not ready yet.
  }

  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (raw) return JSON.parse(raw) as UserIdentity;
  } catch {
    // Ignore.
  }
  return null;
}
