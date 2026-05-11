/**
 * Office.js shim for reading the user's current Word context.
 *
 * Snapshots the current selection and full document body via
 * Word.run(). The App component polls or listens to selection-changed
 * events to refresh.
 *
 * We treat plain text as the canonical exchange format: every read
 * returns ``.text`` for the agent to reason over; every write accepts
 * a string and inserts/replaces as plain text. Word.InsertLocation is
 * the only OOXML-ish concession we lean on.
 */

/* global Office, Word */

export interface WordContext {
  selection: {
    text: string;
    paragraph_count: number;
    word_count: number;
    style_name: string | null;
  };
  document: {
    text: string;
    paragraph_count: number;
    word_count: number;
    style_name: string | null;
  };
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

/**
 * Read both the current selection and the full document body in a
 * single Word.run. Returns plain-text snapshots.
 */
export async function snapshotCurrentContext(): Promise<WordContext | null> {
  if (typeof Word === "undefined" || !Word.run) return null;

  try {
    return await Word.run(async (ctx) => {
      const sel = ctx.document.getSelection();
      const body = ctx.document.body;

      // Load selection text + its style name.
      sel.load(["text", "styleBuiltIn", "style"]);
      body.load(["text"]);

      await ctx.sync();

      const selText = (sel.text as string) || "";
      const docText = (body.text as string) || "";

      const selStyle = (sel.style as string) || (sel.styleBuiltIn as string) || null;

      return {
        selection: {
          text: selText,
          paragraph_count: paragraphCount(selText),
          word_count: wordCount(selText),
          style_name: selStyle,
        },
        document: {
          text: docText,
          paragraph_count: paragraphCount(docText),
          word_count: wordCount(docText),
          style_name: null,
        },
      };
    });
  } catch (e) {
    // soft-fail so the taskpane stays usable even when Word.run is
    // momentarily unavailable (host minimised, taskpane just opened).
    console.warn("snapshotCurrentContext failed:", e);
    return null;
  }
}

/** Insert plain text at the current selection (caret). */
export function insertAtSelection(text: string): Promise<void> {
  return Word.run(async (ctx) => {
    const sel = ctx.document.getSelection();
    sel.insertText(text, Word.InsertLocation.replace);
    await ctx.sync();
  });
}

/** Replace the current selection with new plain text. */
export function replaceSelection(text: string): Promise<void> {
  return Word.run(async (ctx) => {
    const sel = ctx.document.getSelection();
    sel.insertText(text, Word.InsertLocation.replace);
    await ctx.sync();
  });
}

/** Replace the entire document body with new plain text. */
export function replaceWholeDocument(text: string): Promise<void> {
  return Word.run(async (ctx) => {
    const body = ctx.document.body;
    body.clear();
    body.insertText(text, Word.InsertLocation.start);
    await ctx.sync();
  });
}
