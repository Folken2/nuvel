/**
 * Office.js shim for reading the user's current Word context.
 *
 * Snapshots the current selection, surrounding paragraphs, document
 * outline, and basic document metadata via Word.run(). The App
 * component polls or refreshes on demand.
 *
 * Plain text is the canonical exchange format: every read returns
 * `.text` for the agent to reason over; every write accepts a string.
 * Action execution lives in `wordActions.ts`.
 */

/* global Office, Word */

export interface SelectionContext {
  text: string;
  paragraph_count: number;
  word_count: number;
  style_name: string | null;
  is_empty: boolean;
  in_table: boolean;
  in_list: boolean;
  hyperlink: string | null;
  start_offset: number;
  end_offset: number;
}

export interface DocumentContext {
  text: string;
  paragraph_count: number;
  word_count: number;
  style_name: string | null;
  title: string | null;
}

export interface SurroundingContext {
  paragraph_before: string;
  paragraph_at: string;
  paragraph_after: string;
  preceding_heading: { text: string; level: number } | null;
}

export interface HeadingItem {
  text: string;
  level: number;
  index: number;
}

export interface DocumentMeta {
  title: string | null;
  page_count: number | null;
  language: string | null;
  track_changes: boolean;
  comments_count: number;
}

export interface WordContext {
  selection: SelectionContext;
  document: DocumentContext;
  surrounding: SurroundingContext;
  headings: HeadingItem[];
  document_meta: DocumentMeta;
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

const HEADING_STYLES: Record<string, number> = {
  Heading1: 1, Heading2: 2, Heading3: 3, Heading4: 4, Heading5: 5, Heading6: 6,
  "Heading 1": 1, "Heading 2": 2, "Heading 3": 3,
  "Heading 4": 4, "Heading 5": 5, "Heading 6": 6,
};

function headingLevel(style: string | null | undefined): number | null {
  if (!style) return null;
  return HEADING_STYLES[style] ?? null;
}

/**
 * Read the full Word state in a single Word.run. Returns null on
 * soft failure (host minimised, taskpane just opened) so the UI
 * stays responsive.
 */
export async function snapshotCurrentContext(): Promise<WordContext | null> {
  if (typeof Word === "undefined" || !Word.run) return null;

  try {
    return await Word.run(async (ctx) => {
      const sel = ctx.document.getSelection();
      const body = ctx.document.body;
      const paragraphs = body.paragraphs;

      sel.load(["text", "styleBuiltIn", "style", "isEmpty", "hyperlink"]);
      body.load(["text"]);
      paragraphs.load(["text", "styleBuiltIn", "style"]);

      // Document-level properties — guarded with try/catch on host
      // versions that don't expose them.
      const props = ctx.document.properties;
      try { props.load(["title"]); } catch { /* ignore */ }

      await ctx.sync();

      const selText = (sel.text as string) || "";
      const docText = (body.text as string) || "";
      const selStyle = (sel.style as string) || (sel.styleBuiltIn as string) || null;
      const docTitle = (props && (props.title as string)) || null;

      // Surrounding paragraphs: find the paragraph the caret is in by
      // matching the first paragraph that contains the selection text
      // (or, if no selection, by index 0 fallback). Cheap and host-safe.
      const paraItems = paragraphs.items.map((p) => ({
        text: (p.text as string) || "",
        style: (p.style as string) || (p.styleBuiltIn as string) || "Normal",
      }));

      let caretIdx = -1;
      if (selText.trim()) {
        const needle = selText.trim().split(/\s+/).slice(0, 8).join(" ");
        caretIdx = paraItems.findIndex((p) => p.text.includes(needle));
      }
      if (caretIdx < 0) {
        // Fall back to the first non-empty paragraph as "where we are".
        caretIdx = paraItems.findIndex((p) => p.text.trim().length > 0);
      }

      const paragraph_at = caretIdx >= 0 ? paraItems[caretIdx].text : "";
      const paragraph_before = caretIdx > 0 ? paraItems[caretIdx - 1].text : "";
      const paragraph_after = caretIdx >= 0 && caretIdx < paraItems.length - 1
        ? paraItems[caretIdx + 1].text
        : "";

      // Preceding heading: walk upward from caret looking for a
      // Heading{1..6} style.
      let preceding_heading: { text: string; level: number } | null = null;
      for (let i = caretIdx; i >= 0; i--) {
        const lvl = headingLevel(paraItems[i].style);
        if (lvl) {
          preceding_heading = { text: paraItems[i].text.trim(), level: lvl };
          break;
        }
      }

      // Full heading outline.
      const headings: HeadingItem[] = [];
      paraItems.forEach((p, idx) => {
        const lvl = headingLevel(p.style);
        if (lvl && p.text.trim()) {
          headings.push({ text: p.text.trim(), level: lvl, index: idx });
        }
      });

      const isInTable = caretIdx >= 0 && paraItems[caretIdx]
        ? false // host doesn't reliably expose parentTable on paragraph w/o extra call
        : false;
      const isInList = caretIdx >= 0 && paraItems[caretIdx]
        ? /^\s*[-*•]|^\s*\d+[.)]\s/.test(paraItems[caretIdx].text)
        : false;

      let hyperlink: string | null = null;
      try {
        hyperlink = (sel.hyperlink as string) || null;
      } catch { /* host lacks support */ }

      return {
        selection: {
          text: selText,
          paragraph_count: paragraphCount(selText),
          word_count: wordCount(selText),
          style_name: selStyle,
          is_empty: !selText || !selText.trim(),
          in_table: isInTable,
          in_list: isInList,
          hyperlink,
          start_offset: 0,
          end_offset: selText.length,
        },
        document: {
          text: docText,
          paragraph_count: paragraphCount(docText),
          word_count: wordCount(docText),
          style_name: null,
          title: docTitle,
        },
        surrounding: {
          paragraph_before,
          paragraph_at,
          paragraph_after,
          preceding_heading,
        },
        headings,
        document_meta: {
          title: docTitle,
          page_count: null,
          language: null,
          track_changes: false,
          comments_count: 0,
        },
      };
    });
  } catch (e) {
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

export interface DocumentEventHandle {
  /** Tear down the Word event registrations. Always safe to call. */
  dispose: () => Promise<void>;
}

/**
 * Subscribe to Word document events so the taskpane can refresh shared
 * state push-driven instead of polling. Fires ``onChange`` whenever the
 * selection changes or paragraphs are added/changed.
 *
 * Tolerates older hosts: if event registration throws (or the host
 * lacks the requirement set), returns a no-op handle so the caller can
 * fall back to polling.
 */
export async function subscribeToDocumentEvents(
  onChange: () => void
): Promise<DocumentEventHandle> {
  const noop: DocumentEventHandle = { dispose: async () => {} };
  if (typeof Word === "undefined" || !Word.run) return noop;
  try {
    const handlers: Array<{ remove: () => void }> = [];
    await Word.run(async (ctx) => {
      const doc: any = ctx.document;

      const selH = doc.onSelectionChanged?.add?.(async () => {
        try { onChange(); } catch { /* ignore */ }
      });
      if (selH) handlers.push({ remove: () => { try { selH.remove(); } catch {} } });

      const paraAddedH = doc.onParagraphAdded?.add?.(async () => {
        try { onChange(); } catch { /* ignore */ }
      });
      if (paraAddedH) handlers.push({ remove: () => { try { paraAddedH.remove(); } catch {} } });

      const paraChangedH = doc.onParagraphChanged?.add?.(async () => {
        try { onChange(); } catch { /* ignore */ }
      });
      if (paraChangedH) handlers.push({ remove: () => { try { paraChangedH.remove(); } catch {} } });

      await ctx.sync();
    });
    return {
      dispose: async () => {
        await Word.run(async (ctx) => {
          handlers.forEach((h) => h.remove());
          await ctx.sync();
        }).catch(() => undefined);
      },
    };
  } catch (e) {
    console.warn("subscribeToDocumentEvents: host lacks event support, falling back to polling", e);
    return noop;
  }
}
