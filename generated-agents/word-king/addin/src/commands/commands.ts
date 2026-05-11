/**
 * Ribbon command handlers for word-king.
 *
 * Registered via manifest.xml's ExtensionPoints (FunctionName tag).
 * Each function must call event.completed() before returning so Word
 * unfreezes the ribbon.
 */

import { BACKEND_URL, apiHeaders, getSessionId } from "../config/api";

/* global Office, Word */

Office.onReady(() => {
  // No-op: handlers below are registered by name via the manifest.
});

interface SelectionSnapshot {
  text: string;
  paragraph_count: number;
  word_count: number;
  style_name: string | null;
}

function wordCount(s: string): number {
  if (!s) return 0;
  const m = s.match(/\b[\w']+\b/g);
  return m ? m.length : 0;
}

function paragraphCount(s: string): number {
  if (!s) return 0;
  return s.split(/\n\s*\n/).filter((p) => p.trim().length > 0).length;
}

async function readCurrentSelection(): Promise<SelectionSnapshot | null> {
  if (typeof Word === "undefined" || !Word.run) return null;
  try {
    return await Word.run(async (ctx) => {
      const sel = ctx.document.getSelection();
      sel.load(["text", "styleBuiltIn", "style"]);
      await ctx.sync();
      const text = (sel.text as string) || "";
      const style = (sel.style as string) || (sel.styleBuiltIn as string) || null;
      return {
        text,
        paragraph_count: paragraphCount(text),
        word_count: wordCount(text),
        style_name: style,
      };
    });
  } catch (e) {
    console.warn("readCurrentSelection failed:", e);
    return null;
  }
}

async function replaceSelectionWith(text: string): Promise<void> {
  await Word.run(async (ctx) => {
    const sel = ctx.document.getSelection();
    sel.insertText(text, Word.InsertLocation.replace);
    await ctx.sync();
  });
}

/**
 * Quick action: grab the current selection, ask the agent to rewrite
 * it in the user's voice keeping length and meaning, then replace the
 * selection with the result. Surfaces a brief notification so the user
 * knows it ran.
 */
async function rewriteCurrentSelection(event: Office.AddinCommands.Event) {
  try {
    const sel = await readCurrentSelection();
    if (!sel || !sel.text.trim()) {
      notify("Select some text first, then run Rewrite selection.");
      event.completed();
      return;
    }

    const res = await fetch(`${BACKEND_URL}/api/word/chat`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        session_id: getSessionId(),
        user_id: (Office.context as any)?.document?.url || "word-user",
        prompt: "Rewrite this in my voice, keep the meaning and the length.",
        selection: sel,
      }),
    });

    if (!res.ok) {
      notify(`Rewrite failed: HTTP ${res.status}`);
      event.completed();
      return;
    }
    const data = await res.json();
    const rewritten = (data.message || "").trim();
    if (!rewritten) {
      notify("Agent returned no text. Open the taskpane for details.");
      event.completed();
      return;
    }

    await replaceSelectionWith(rewritten);

    // Fire-and-forget: tell the backend the user took this draft so
    // the style memory learns from it.
    fetch(`${BACKEND_URL}/api/word/learn-passage`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ passage: rewritten, source: "ribbon-rewrite-accepted", note: "" }),
    }).catch(() => undefined);

    const preview = rewritten.slice(0, 140).replace(/\s+/g, " ");
    notify(`Rewrote: "${preview}${rewritten.length > 140 ? "…" : ""}"`);
  } catch (e: any) {
    notify(`Rewrite failed: ${e?.message || e}`);
  } finally {
    event.completed();
  }
}

function notify(message: string) {
  // Word doesn't have item-level notificationMessages like Outlook.
  // Fall back to console + (best-effort) Office.context.ui dialog notifier
  // if the host supports it.
  console.log("[word-king]", message);
  try {
    const settings = (Office.context as any)?.document?.settings;
    if (settings && typeof settings.set === "function") {
      settings.set("word-king.last_notification", message);
      settings.saveAsync?.(() => undefined);
    }
  } catch {
    /* ignore */
  }
}

// Expose for the manifest's <FunctionName> hooks.
(window as any).rewriteCurrentSelection = rewriteCurrentSelection;
Office.actions.associate("rewriteCurrentSelection", rewriteCurrentSelection);
