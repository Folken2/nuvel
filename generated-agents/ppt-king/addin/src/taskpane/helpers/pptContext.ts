/**
 * Office.js shim for reading and mutating the user's current PowerPoint
 * context. All operations use the PowerPoint.run(...) batched call style.
 *
 * Three shapes:
 *   SelectedShape   — geometry + text + name for a currently-selected shape
 *   CurrentSlide    — the user has a slide selected (1-based index in PPT,
 *                     we expose 0-based to the backend) plus selected_shapes
 *   DeckOutline     — every slide's index + title + bullet count + has-notes
 *
 * Plus an action dispatcher: ``executeAction`` takes one of the JSON
 * action dicts the agent enqueues (see ppt_king/tools/action_tools.py)
 * and runs it against the deck.
 */

/* global Office, PowerPoint */

export interface SelectedShape {
  name: string;
  type: string;
  text: string;
  left: number;
  top: number;
  width: number;
  height: number;
  is_placeholder: boolean;
}

export interface CurrentSlide {
  index: number;
  slide_id: string;
  title: string;
  bullets: string[];
  notes: string;
  layout_name: string;
  shape_count: number;
  selected_shapes: SelectedShape[];
}

export interface DeckSlideSummary {
  index: number;
  slide_id: string;
  title: string;
  bullet_count: number;
  has_notes: boolean;
}

export interface DeckOutline {
  slide_count: number;
  slides: DeckSlideSummary[];
}

export interface PptContext {
  current_slide: CurrentSlide | null;
  deck_outline: DeckOutline | null;
}

export interface PptAction {
  type: string;
  [key: string]: any;
}

export interface ActionResult {
  type: string;
  status: "ok" | "error" | "skip";
  message?: string;
  summary?: string;
  slide_index?: number;
}

function splitBullets(text: string): string[] {
  return (text || "")
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function readShapeText(shape: any): string {
  try {
    const tf = shape?.textFrame;
    if (!tf) return "";
    return tf.textRange?.text || "";
  } catch {
    return "";
  }
}

function findTitleShape(shapes: any[]): any | null {
  for (const sh of shapes) {
    const name = (sh?.name || "").toLowerCase();
    if (name.includes("title") && !name.includes("subtitle")) return sh;
  }
  return null;
}

function findContentShape(shapes: any[]): any | null {
  for (const sh of shapes) {
    const name = (sh?.name || "").toLowerCase();
    if (name.includes("content") || name.includes("body") || name.includes("placeholder")) {
      if (!name.includes("title")) return sh;
    }
  }
  for (const sh of shapes) {
    const name = (sh?.name || "").toLowerCase();
    if (name.includes("title")) continue;
    if (readShapeText(sh)) return sh;
  }
  return null;
}

function findShapeByName(shapes: any[], name: string): any | null {
  const target = (name || "").toLowerCase();
  for (const sh of shapes) {
    if ((sh?.name || "").toLowerCase() === target) return sh;
  }
  for (const sh of shapes) {
    if ((sh?.name || "").toLowerCase().includes(target)) return sh;
  }
  return null;
}

/** Snapshot the active slide + the whole deck outline in one batch. */
export async function snapshotCurrentContext(): Promise<PptContext> {
  try {
    return await PowerPoint.run(async (ctx) => {
      const pres = ctx.presentation;
      const slides = pres.slides;
      slides.load("items");
      const selected = pres.getSelectedSlides();
      selected.load("items");
      let selectedShapesCol: any = null;
      try {
        selectedShapesCol = (pres as any).getSelectedShapes?.();
        if (selectedShapesCol?.load) selectedShapesCol.load("items");
      } catch { /* not all hosts expose this */ }
      await ctx.sync();

      const allSlides = slides.items || [];
      const selectedSlides = selected.items || [];
      const activeSlide = selectedSlides[0] || allSlides[0] || null;

      for (const sl of allSlides) {
        sl.shapes.load("items/name,id");
        sl.notesSlide.load("notesPlaceholder");
      }
      if (activeSlide) {
        activeSlide.shapes.load(
          "items/name,items/id,items/type,items/left,items/top," +
          "items/width,items/height,items/textFrame/textRange/text"
        );
        activeSlide.notesSlide.load("notesPlaceholder/textFrame/textRange/text");
        activeSlide.layout?.load("name");
      }
      await ctx.sync();

      const outlineSlides: DeckSlideSummary[] = [];
      for (let i = 0; i < allSlides.length; i++) {
        const sl = allSlides[i];
        const shapes = sl.shapes.items || [];
        const titleSh = findTitleShape(shapes);
        if (titleSh) titleSh.textFrame.textRange.load("text");
        const contentSh = findContentShape(shapes);
        if (contentSh && contentSh !== titleSh) contentSh.textFrame.textRange.load("text");
      }
      await ctx.sync();

      for (let i = 0; i < allSlides.length; i++) {
        const sl = allSlides[i];
        const shapes = sl.shapes.items || [];
        const titleSh = findTitleShape(shapes);
        const contentSh = findContentShape(shapes);
        const title = titleSh ? readShapeText(titleSh).trim() : "";
        const bulletText = contentSh && contentSh !== titleSh ? readShapeText(contentSh) : "";
        const bulletCount = splitBullets(bulletText).length;
        const notesText = sl.notesSlide?.notesPlaceholder?.textFrame?.textRange?.text || "";
        outlineSlides.push({
          index: i,
          slide_id: String(sl.id || ""),
          title,
          bullet_count: bulletCount,
          has_notes: !!(notesText && notesText.trim()),
        });
      }

      const deck_outline: DeckOutline = {
        slide_count: outlineSlides.length,
        slides: outlineSlides,
      };

      // Build selected_shapes (limited to shapes on the active slide).
      const selectedShapeIds = new Set<string>();
      try {
        const items = selectedShapesCol?.items || [];
        for (const sh of items) selectedShapeIds.add(String(sh.id || ""));
      } catch { /* ignore */ }

      let current_slide: CurrentSlide | null = null;
      if (activeSlide) {
        const idx = allSlides.findIndex((s) => s.id === activeSlide.id);
        const shapes = activeSlide.shapes.items || [];
        const titleSh = findTitleShape(shapes);
        const contentSh = findContentShape(shapes);
        const title = titleSh ? readShapeText(titleSh).trim() : "";
        const bulletText = contentSh && contentSh !== titleSh ? readShapeText(contentSh) : "";
        const bullets = splitBullets(bulletText);
        const notes = activeSlide.notesSlide?.notesPlaceholder?.textFrame?.textRange?.text || "";
        const layout_name = activeSlide.layout?.name || "";

        const selected_shapes: SelectedShape[] = [];
        for (const sh of shapes) {
          const shId = String(sh.id || "");
          if (!selectedShapeIds.has(shId)) continue;
          const name = sh.name || "";
          selected_shapes.push({
            name,
            type: String(sh.type || ""),
            text: readShapeText(sh),
            left: Number(sh.left ?? 0),
            top: Number(sh.top ?? 0),
            width: Number(sh.width ?? 0),
            height: Number(sh.height ?? 0),
            is_placeholder:
              /placeholder|title|content|body|subtitle/i.test(name),
          });
        }

        current_slide = {
          index: idx >= 0 ? idx : 0,
          slide_id: String(activeSlide.id || ""),
          title,
          bullets,
          notes: (notes || "").trim(),
          layout_name,
          shape_count: shapes.length,
          selected_shapes,
        };
      }

      return { current_slide, deck_outline };
    });
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("snapshotCurrentContext failed:", e);
    return { current_slide: null, deck_outline: null };
  }
}

/** Replace title/bullets/notes for slide at slideIndex. */
export async function setSlideContent(
  slideIndex: number,
  title: string,
  bullets: string[],
  notes: string
): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const pres = ctx.presentation;
    const slides = pres.slides;
    slides.load("items");
    await ctx.sync();
    const sl = (slides.items || [])[slideIndex];
    if (!sl) throw new Error(`No slide at index ${slideIndex}.`);
    sl.shapes.load("items/name");
    await ctx.sync();
    const shapes = sl.shapes.items || [];
    const titleSh = findTitleShape(shapes);
    const contentSh = findContentShape(shapes);
    if (titleSh) {
      try { titleSh.textFrame.textRange.text = title || ""; } catch { /* */ }
    }
    if (contentSh && contentSh !== titleSh) {
      const joined = (bullets || []).join("\n");
      try { contentSh.textFrame.textRange.text = joined; } catch { /* */ }
    }
    try {
      const np = sl.notesSlide?.notesPlaceholder;
      if (np) np.textFrame.textRange.text = notes || "";
    } catch { /* */ }
    await ctx.sync();
  });
}

/** Insert a brand-new slide with title / bullets / notes after `afterIndex`. */
export async function insertNewSlide(
  afterIndex: number,
  title: string,
  bullets: string[],
  notes: string
): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const pres = ctx.presentation;
    const slides = pres.slides;
    slides.load("items");
    await ctx.sync();

    let newSlide: any;
    try {
      newSlide = slides.add();
    } catch {
      throw new Error("This PowerPoint host does not support programmatic slide insertion.");
    }
    await ctx.sync();

    slides.load("items");
    await ctx.sync();
    const all = slides.items || [];
    const lastIndex = all.length - 1;
    const targetIndex = Math.min(lastIndex, Math.max(0, afterIndex + 1));
    if (lastIndex !== targetIndex && typeof newSlide.moveTo === "function") {
      try { newSlide.moveTo(targetIndex); } catch { /* */ }
      await ctx.sync();
    }

    newSlide.shapes.load("items/name");
    await ctx.sync();
    const shapes = newSlide.shapes.items || [];
    const titleSh = findTitleShape(shapes);
    const contentSh = findContentShape(shapes);
    if (titleSh) {
      try { titleSh.textFrame.textRange.text = title || ""; } catch { /* */ }
    }
    if (contentSh && contentSh !== titleSh) {
      try { contentSh.textFrame.textRange.text = (bullets || []).join("\n"); } catch { /* */ }
    }
    try {
      const np = newSlide.notesSlide?.notesPlaceholder;
      if (np) np.textFrame.textRange.text = notes || "";
    } catch { /* */ }
    await ctx.sync();
  });
}

/** Move a slide from `fromIndex` to `toIndex`. */
export async function reorderSlides(fromIndex: number, toIndex: number): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const pres = ctx.presentation;
    const slides = pres.slides;
    slides.load("items");
    await ctx.sync();
    const all = slides.items || [];
    const sl = all[fromIndex];
    if (!sl) throw new Error(`No slide at index ${fromIndex}.`);
    if (typeof sl.moveTo !== "function") {
      throw new Error("This PowerPoint host does not support programmatic slide reordering.");
    }
    sl.moveTo(toIndex);
    await ctx.sync();
  });
}

/** Duplicate the slide at `slideIndex`. The clone lands right after the original. */
export async function duplicateSlide(slideIndex: number): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const slides = ctx.presentation.slides;
    slides.load("items");
    await ctx.sync();
    const sl = (slides.items || [])[slideIndex];
    if (!sl) throw new Error(`No slide at index ${slideIndex}.`);
    if (typeof (sl as any).duplicate !== "function") {
      throw new Error("PowerPoint host does not support slide.duplicate().");
    }
    const dup = (sl as any).duplicate();
    await ctx.sync();
    if (typeof dup?.moveTo === "function") {
      try { dup.moveTo(slideIndex + 1); await ctx.sync(); } catch { /* */ }
    }
  });
}

/** Delete the slide at `slideIndex`. */
export async function deleteSlide(slideIndex: number): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const slides = ctx.presentation.slides;
    slides.load("items");
    await ctx.sync();
    const sl = (slides.items || [])[slideIndex];
    if (!sl) throw new Error(`No slide at index ${slideIndex}.`);
    if (typeof (sl as any).delete !== "function") {
      throw new Error("PowerPoint host does not support slide.delete().");
    }
    (sl as any).delete();
    await ctx.sync();
  });
}

/** Set speaker notes on slide at `slideIndex`. */
export async function setSlideNotes(slideIndex: number, notes: string): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const slides = ctx.presentation.slides;
    slides.load("items");
    await ctx.sync();
    const sl = (slides.items || [])[slideIndex];
    if (!sl) throw new Error(`No slide at index ${slideIndex}.`);
    const np = sl.notesSlide?.notesPlaceholder;
    if (!np) throw new Error(`No notes placeholder on slide ${slideIndex}.`);
    np.textFrame.textRange.text = notes || "";
    await ctx.sync();
  });
}

/** Replace the text inside a named shape on a slide. */
export async function setShapeText(
  slideIndex: number,
  shapeName: string,
  text: string
): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const slides = ctx.presentation.slides;
    slides.load("items");
    await ctx.sync();
    const sl = (slides.items || [])[slideIndex];
    if (!sl) throw new Error(`No slide at index ${slideIndex}.`);
    sl.shapes.load("items/name");
    await ctx.sync();
    const shape = findShapeByName(sl.shapes.items || [], shapeName);
    if (!shape) throw new Error(`No shape named "${shapeName}" on slide ${slideIndex}.`);
    shape.textFrame.textRange.text = text || "";
    await ctx.sync();
  });
}

/** Find/replace across the deck (or one slide). Returns count of replacements. */
export async function replaceTextInDeck(
  find: string,
  replace: string,
  opts: { scope?: "deck" | "slide"; slideIndex?: number; matchCase?: boolean } = {}
): Promise<number> {
  if (!find) return 0;
  const scope = opts.scope || "deck";
  const matchCase = !!opts.matchCase;
  const flags = matchCase ? "g" : "gi";
  const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(escape(find), flags);
  let total = 0;
  await PowerPoint.run(async (ctx) => {
    const slides = ctx.presentation.slides;
    slides.load("items");
    await ctx.sync();
    const all = slides.items || [];
    const targets = scope === "slide"
      ? [all[opts.slideIndex ?? -1]].filter(Boolean)
      : all;
    for (const sl of targets) {
      sl.shapes.load("items/name");
    }
    await ctx.sync();
    for (const sl of targets) {
      for (const sh of sl.shapes.items || []) {
        try { sh.textFrame.textRange.load("text"); } catch { /* */ }
      }
    }
    await ctx.sync();
    for (const sl of targets) {
      for (const sh of sl.shapes.items || []) {
        try {
          const tr = sh.textFrame?.textRange;
          const original = tr?.text || "";
          if (!original) continue;
          const replaced = original.replace(re, () => { total++; return replace; });
          if (replaced !== original) tr.text = replaced;
        } catch { /* skip non-text shapes */ }
      }
    }
    await ctx.sync();
  });
  return total;
}

/** Insert a freeform text box on a slide. */
export async function addTextBox(
  slideIndex: number,
  text: string,
  left = 50,
  top = 50,
  width = 400,
  height = 80
): Promise<void> {
  await PowerPoint.run(async (ctx) => {
    const slides = ctx.presentation.slides;
    slides.load("items");
    await ctx.sync();
    const sl = (slides.items || [])[slideIndex];
    if (!sl) throw new Error(`No slide at index ${slideIndex}.`);
    const shapes: any = sl.shapes;
    const tb = shapes.addTextBox
      ? shapes.addTextBox(text || "", { left, top, width, height })
      : null;
    if (!tb) throw new Error("PowerPoint host does not support addTextBox.");
    await ctx.sync();
  });
}

/** Move the user's selection / view to a given slide index. Best-effort. */
export async function selectSlide(slideIndex: number): Promise<void> {
  try {
    await PowerPoint.run(async (ctx) => {
      const slides = ctx.presentation.slides;
      slides.load("items");
      await ctx.sync();
      const sl = (slides.items || [])[slideIndex];
      if (!sl) return;
      if (typeof (ctx.presentation as any).setSelectedSlides === "function") {
        try { (ctx.presentation as any).setSelectedSlides([sl.id]); } catch { /* */ }
      }
      await ctx.sync();
    });
  } catch { /* ignore */ }
}

/**
 * Execute one queued agent action against PowerPoint.
 * Returns a structured result the addin can surface in the UI
 * and forward back to the backend's recent-edits log.
 */
export async function executeAction(action: PptAction): Promise<ActionResult> {
  const type = action?.type;
  try {
    switch (type) {
      case "apply_slide": {
        const idx = Number(action.slide_index);
        await setSlideContent(idx, action.title || "", action.bullets || [], action.notes || "");
        return {
          type,
          status: "ok",
          slide_index: idx,
          summary: `Applied "${(action.title || "").slice(0, 40)}" to slide ${idx + 1}`,
        };
      }
      case "insert_slide": {
        const after = Number(action.after_index ?? -1);
        await insertNewSlide(after, action.title || "", action.bullets || [], action.notes || "");
        return {
          type,
          status: "ok",
          slide_index: after + 1,
          summary: `Inserted "${(action.title || "").slice(0, 40)}" after slide ${after + 1}`,
        };
      }
      case "duplicate_slide": {
        const idx = Number(action.slide_index);
        await duplicateSlide(idx);
        return { type, status: "ok", slide_index: idx, summary: `Duplicated slide ${idx + 1}` };
      }
      case "delete_slide": {
        const idx = Number(action.slide_index);
        await deleteSlide(idx);
        return { type, status: "ok", slide_index: idx, summary: `Deleted slide ${idx + 1}` };
      }
      case "move_slide": {
        const from = Number(action.from_index);
        const to = Number(action.to_index);
        await reorderSlides(from, to);
        return { type, status: "ok", slide_index: to, summary: `Moved slide ${from + 1} → ${to + 1}` };
      }
      case "set_notes": {
        const idx = Number(action.slide_index);
        await setSlideNotes(idx, action.notes || "");
        return { type, status: "ok", slide_index: idx, summary: `Set notes on slide ${idx + 1}` };
      }
      case "set_shape_text": {
        const idx = Number(action.slide_index);
        await setShapeText(idx, action.shape_name || "", action.text || "");
        return {
          type,
          status: "ok",
          slide_index: idx,
          summary: `Updated "${action.shape_name}" on slide ${idx + 1}`,
        };
      }
      case "replace_text": {
        const count = await replaceTextInDeck(
          action.find || "",
          action.replace || "",
          {
            scope: action.scope || "deck",
            slideIndex: Number(action.slide_index ?? -1),
            matchCase: !!action.match_case,
          }
        );
        return {
          type,
          status: count > 0 ? "ok" : "skip",
          summary: `Replaced "${action.find}" → "${action.replace}" (${count} match${count === 1 ? "" : "es"})`,
        };
      }
      case "add_text_box": {
        const idx = Number(action.slide_index);
        await addTextBox(
          idx,
          action.text || "",
          Number(action.left ?? 50),
          Number(action.top ?? 50),
          Number(action.width ?? 400),
          Number(action.height ?? 80)
        );
        return { type, status: "ok", slide_index: idx, summary: `Added text box to slide ${idx + 1}` };
      }
      case "request_refresh": {
        return { type, status: "ok", summary: "Snapshot refresh requested" };
      }
      default:
        return { type: type || "unknown", status: "error", message: `Unknown action type: ${type}` };
    }
  } catch (e) {
    return {
      type: type || "unknown",
      status: "error",
      message: e instanceof Error ? e.message : String(e),
    };
  }
}

/** Execute a queue of actions in order. Stops on first error unless `continueOnError`. */
export async function executeActionQueue(
  actions: PptAction[],
  opts: { continueOnError?: boolean } = {}
): Promise<ActionResult[]> {
  const results: ActionResult[] = [];
  for (const a of actions || []) {
    const r = await executeAction(a);
    results.push(r);
    if (r.status === "error" && !opts.continueOnError) break;
  }
  return results;
}
