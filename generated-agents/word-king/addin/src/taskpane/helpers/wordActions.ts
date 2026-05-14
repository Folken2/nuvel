/**
 * Office.js dispatcher for actions the agent enqueues.
 *
 * The backend's `/api/word/chat[/stream]` `final` event includes
 * `actions: [{kind, params, description}]`. This module runs them in
 * order and returns a per-action log entry the taskpane posts back to
 * `/api/word/edits` so the agent's `get_recent_edits` tool sees them
 * next turn.
 *
 * Keep `kind` values in sync with `word_king/tools/word_actions.py`.
 */

/* global Word */

export interface WordAction {
  kind: string;
  params: Record<string, any>;
  description?: string;
}

export interface ExecutedEdit {
  kind: string;
  summary: string;
  at: string;
  ok: boolean;
  error?: string;
}

const HEADING_STYLE_BY_LEVEL: Record<number, Word.BuiltInStyleName> = {
  1: Word.BuiltInStyleName.heading1,
  2: Word.BuiltInStyleName.heading2,
  3: Word.BuiltInStyleName.heading3,
  4: Word.BuiltInStyleName.heading4,
  5: Word.BuiltInStyleName.heading5,
  6: Word.BuiltInStyleName.heading6,
};

const STYLE_BY_NAME: Record<string, Word.BuiltInStyleName> = {
  Normal: Word.BuiltInStyleName.normal,
  Title: Word.BuiltInStyleName.title,
  Subtitle: Word.BuiltInStyleName.subtitle,
  Heading1: Word.BuiltInStyleName.heading1,
  Heading2: Word.BuiltInStyleName.heading2,
  Heading3: Word.BuiltInStyleName.heading3,
  Heading4: Word.BuiltInStyleName.heading4,
  Heading5: Word.BuiltInStyleName.heading5,
  Heading6: Word.BuiltInStyleName.heading6,
  Quote: Word.BuiltInStyleName.quote,
  IntenseQuote: Word.BuiltInStyleName.intenseQuote,
  ListParagraph: Word.BuiltInStyleName.listParagraph,
  Emphasis: Word.BuiltInStyleName.emphasis,
  Strong: Word.BuiltInStyleName.strong,
  NoSpacing: Word.BuiltInStyleName.noSpacing,
};

async function runInsertText(p: any) {
  const text: string = p.text || "";
  const location: string = (p.location || "selection").toLowerCase();
  await Word.run(async (ctx) => {
    if (location === "start") {
      ctx.document.body.insertText(text, Word.InsertLocation.start);
    } else if (location === "end") {
      ctx.document.body.insertText(text, Word.InsertLocation.end);
    } else {
      ctx.document.getSelection().insertText(text, Word.InsertLocation.replace);
    }
    await ctx.sync();
  });
}

async function runReplaceSelection(p: any) {
  const text: string = p.text || "";
  await Word.run(async (ctx) => {
    ctx.document.getSelection().insertText(text, Word.InsertLocation.replace);
    await ctx.sync();
  });
}

async function runApplyFormatting(p: any) {
  await Word.run(async (ctx) => {
    const range = p.target === "paragraph"
      ? ctx.document.getSelection().paragraphs.getFirst().getRange()
      : ctx.document.getSelection();
    const font = (range as any).font;
    if (font) {
      if (typeof p.bold === "boolean") font.bold = p.bold;
      if (typeof p.italic === "boolean") font.italic = p.italic;
      if (typeof p.underline === "boolean") {
        font.underline = p.underline ? Word.UnderlineType.single : Word.UnderlineType.none;
      }
    }
    if (p.style && STYLE_BY_NAME[p.style]) {
      // Style applies to the containing paragraph.
      const para = ctx.document.getSelection().paragraphs.getFirst();
      (para as any).styleBuiltIn = STYLE_BY_NAME[p.style];
    }
    await ctx.sync();
  });
}

async function runInsertHeading(p: any) {
  const text: string = p.text;
  const level: number = p.level || 2;
  await Word.run(async (ctx) => {
    const sel = ctx.document.getSelection();
    const para = sel.insertParagraph(text, Word.InsertLocation.before);
    (para as any).styleBuiltIn = HEADING_STYLE_BY_LEVEL[level] || HEADING_STYLE_BY_LEVEL[2];
    await ctx.sync();
  });
}

async function runInsertTable(p: any) {
  const rows: string[][] = p.rows;
  const hasHeader: boolean = !!p.has_header;
  await Word.run(async (ctx) => {
    const sel = ctx.document.getSelection();
    const table = sel.insertTable(rows.length, rows[0].length, Word.InsertLocation.after, rows);
    if (hasHeader && table.rows.items.length > 0) {
      table.rows.load("items");
      await ctx.sync();
      const header = table.rows.items[0];
      header.font.bold = true;
    }
    await ctx.sync();
  });
}

async function runInsertComment(p: any) {
  await Word.run(async (ctx) => {
    const range = p.on === "paragraph"
      ? ctx.document.getSelection().paragraphs.getFirst().getRange()
      : ctx.document.getSelection();
    // insertComment is available on Range in Word API 1.4+.
    if (typeof (range as any).insertComment === "function") {
      (range as any).insertComment(p.text);
    } else {
      // Fallback — append the comment text as a parenthetical so the user still sees it.
      (range as any).insertText(` [${p.text}]`, Word.InsertLocation.end);
    }
    await ctx.sync();
  });
}

async function runFindAndReplace(p: any) {
  const find: string = p.find || "";
  const replace: string = p.replace ?? "";
  await Word.run(async (ctx) => {
    const results = ctx.document.body.search(find, {
      matchCase: !!p.match_case,
      matchWholeWord: !!p.whole_word,
    });
    results.load("items");
    await ctx.sync();
    for (const r of results.items) {
      r.insertText(replace, Word.InsertLocation.replace);
    }
    await ctx.sync();
  });
}

async function runNavigateToHeading(p: any) {
  const needle = (p.heading_text || "").toLowerCase();
  if (!needle) return;
  await Word.run(async (ctx) => {
    const paragraphs = ctx.document.body.paragraphs;
    paragraphs.load(["text", "styleBuiltIn"]);
    await ctx.sync();
    const match = paragraphs.items.find((p2) => {
      const text = (p2.text || "").toLowerCase();
      const style = (p2 as any).styleBuiltIn as string;
      return text.includes(needle) && /heading\d/i.test(style || "");
    });
    if (match) {
      match.select();
      await ctx.sync();
    }
  });
}

async function runDeleteSelection() {
  await Word.run(async (ctx) => {
    ctx.document.getSelection().delete();
    await ctx.sync();
  });
}

/** Dispatch a single action; returns a log entry. */
export async function executeWordAction(action: WordAction): Promise<ExecutedEdit> {
  const at = new Date().toISOString();
  const summary = action.description || action.kind;
  try {
    switch (action.kind) {
      case "insert_text": await runInsertText(action.params); break;
      case "replace_selection": await runReplaceSelection(action.params); break;
      case "apply_formatting": await runApplyFormatting(action.params); break;
      case "insert_heading": await runInsertHeading(action.params); break;
      case "insert_table": await runInsertTable(action.params); break;
      case "insert_comment": await runInsertComment(action.params); break;
      case "find_and_replace": await runFindAndReplace(action.params); break;
      case "navigate_to_heading": await runNavigateToHeading(action.params); break;
      case "delete_selection": await runDeleteSelection(); break;
      case "refresh_context":
        // Handled at the App layer (just resnapshot + push). No-op here.
        break;
      default:
        return { kind: action.kind, summary, at, ok: false, error: `Unknown action kind: ${action.kind}` };
    }
    return { kind: action.kind, summary, at, ok: true };
  } catch (e: any) {
    return {
      kind: action.kind,
      summary,
      at,
      ok: false,
      error: e?.message || String(e),
    };
  }
}

/** Run all queued actions sequentially; return per-action log entries. */
export async function executeActionQueue(actions: WordAction[]): Promise<ExecutedEdit[]> {
  const log: ExecutedEdit[] = [];
  for (const a of actions || []) {
    log.push(await executeWordAction(a));
  }
  return log;
}
