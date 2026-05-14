/**
 * Ribbon + shortcut + event-activation command handlers for word-king.
 *
 * Registered via manifest.xml's ExtensionPoints (FunctionName tag) or
 * manifest.json's extensions.runtimes.actions (executeFunction).
 *
 * Each function must call event.completed() before returning so Word
 * unfreezes the ribbon. Handlers fail open — never block the user on a
 * backend hiccup.
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

/**
 * Shortcut + JSON-manifest action: open the task pane.
 *
 * Bound to Ctrl+Alt+W via the JSON manifest's keyboardShortcuts block.
 * Uses Office.addin.showAsTaskpane when available (shared runtime) and
 * falls back to a no-op so XML-manifest sideloads stay unaffected.
 */
async function showTaskpane(event?: Office.AddinCommands.Event) {
  try {
    const addin = (Office as any)?.addin;
    if (addin && typeof addin.showAsTaskpane === "function") {
      await addin.showAsTaskpane();
    }
  } catch (e) {
    console.warn("[word-king] showTaskpane failed:", e);
  } finally {
    event?.completed?.();
  }
}

interface DocumentSnapshot {
  title: string | null;
  word_count: number;
  paragraph_count: number;
  headings: { text: string; level: number; index: number }[];
}

const HEADING_STYLE_LEVELS: Record<string, number> = {
  Heading1: 1, Heading2: 2, Heading3: 3, Heading4: 4, Heading5: 5, Heading6: 6,
  "Heading 1": 1, "Heading 2": 2, "Heading 3": 3,
  "Heading 4": 4, "Heading 5": 5, "Heading 6": 6,
};

async function captureDocumentSnapshot(): Promise<DocumentSnapshot | null> {
  if (typeof Word === "undefined" || !Word.run) return null;
  try {
    return await Word.run(async (ctx) => {
      const body = ctx.document.body;
      const paragraphs = body.paragraphs;
      const props = ctx.document.properties;
      body.load(["text"]);
      paragraphs.load(["text", "styleBuiltIn", "style"]);
      try { props.load(["title"]); } catch { /* ignore */ }
      await ctx.sync();

      const text = (body.text as string) || "";
      const title = (props && (props.title as string)) || null;
      const headings: { text: string; level: number; index: number }[] = [];
      paragraphs.items.forEach((p, idx) => {
        const style = (p.style as string) || (p.styleBuiltIn as string) || "";
        const level = HEADING_STYLE_LEVELS[style];
        const t = ((p.text as string) || "").trim();
        if (level && t) headings.push({ text: t, level, index: idx });
      });
      return {
        title,
        word_count: wordCount(text),
        paragraph_count: paragraphCount(text),
        headings: headings.slice(0, 50),
      };
    });
  } catch (e) {
    console.warn("[word-king] captureDocumentSnapshot failed:", e);
    return null;
  }
}

async function postDocumentOpened(snap: DocumentSnapshot | null, isNew: boolean): Promise<void> {
  if (!snap) return;
  try {
    await fetch(`${BACKEND_URL}/api/word/document-opened`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        session_id: getSessionId(),
        user_id: (Office.context as any)?.document?.url || "word-user",
        is_new: isNew,
        snapshot: snap,
      }),
    });
  } catch (e) {
    console.warn("[word-king] postDocumentOpened failed:", e);
  }
}

/**
 * Event-based activation handler — fires when a document opens.
 *
 * NOTE: as of writing the unified-manifest equivalent for Word's
 * OnDocumentOpened event is "Not yet supported" by Microsoft, so this
 * handler is currently invoked by the taskpane on first load. Kept
 * here so it's a one-line manifest change once the schema lands.
 */
async function onDocumentOpenedHandler(event: Office.AddinCommands.Event) {
  try {
    const snap = await captureDocumentSnapshot();
    await postDocumentOpened(snap, false);
  } catch (e) {
    console.warn("[word-king] onDocumentOpenedHandler failed:", e);
  } finally {
    event?.completed?.();
  }
}

async function onNewDocumentCreatedHandler(event: Office.AddinCommands.Event) {
  try {
    const snap = await captureDocumentSnapshot();
    await postDocumentOpened(snap, true);
  } catch (e) {
    console.warn("[word-king] onNewDocumentCreatedHandler failed:", e);
  } finally {
    event?.completed?.();
  }
}

// Expose for the manifest's <FunctionName> hooks.
(window as any).rewriteCurrentSelection = rewriteCurrentSelection;
(window as any).showTaskpane = showTaskpane;
(window as any).onDocumentOpenedHandler = onDocumentOpenedHandler;
(window as any).onNewDocumentCreatedHandler = onNewDocumentCreatedHandler;
Office.actions.associate("rewriteCurrentSelection", rewriteCurrentSelection);
Office.actions.associate("showTaskpane", showTaskpane);
Office.actions.associate("onDocumentOpenedHandler", onDocumentOpenedHandler);
Office.actions.associate("onNewDocumentCreatedHandler", onNewDocumentCreatedHandler);
