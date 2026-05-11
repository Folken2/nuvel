/**
 * Office.js shim for reading and mutating the user's current PowerPoint
 * context. All operations use the PowerPoint.run(...) batched call style.
 *
 * Two shapes:
 *   CurrentSlide    — the user has a slide selected (1-based index in PPT,
 *                     we expose 0-based to the backend)
 *   DeckOutline     — every slide's index + title + bullet count + has-notes
 *
 * We do NOT subscribe to selection-change events here; the App component
 * polls or refreshes on focus.
 */

/* global Office, PowerPoint */

export interface CurrentSlide {
  index: number;
  title: string;
  bullets: string[];
  notes: string;
  layout_name: string;
}

export interface DeckSlideSummary {
  index: number;
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

/** Split a textRun-style multiline string into bullet lines. */
function splitBullets(text: string): string[] {
  return (text || "")
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

/** Concatenate all text-runs from a shape's textFrame. */
function readShapeText(shape: any): string {
  try {
    const tf = shape?.textFrame;
    if (!tf) return "";
    return tf.textRange?.text || "";
  } catch {
    return "";
  }
}

/** Best-effort title detector: pick the shape whose name contains "Title". */
function findTitleShape(shapes: any[]): any | null {
  for (const sh of shapes) {
    const name = (sh?.name || "").toLowerCase();
    if (name.includes("title") && !name.includes("subtitle")) return sh;
  }
  return null;
}

/** Best-effort content detector: prefer the shape named "Content"/"Body";
 *  fall back to the first non-title shape that has text. */
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

/** Snapshot the active slide + the whole deck outline in one batch. */
export async function snapshotCurrentContext(): Promise<PptContext> {
  try {
    return await PowerPoint.run(async (ctx) => {
      const pres = ctx.presentation;
      const slides = pres.slides;
      slides.load("items");
      const selected = pres.getSelectedSlides();
      selected.load("items");
      await ctx.sync();

      const allSlides = slides.items || [];
      const selectedSlides = selected.items || [];
      const activeSlide = selectedSlides[0] || allSlides[0] || null;

      // Build deck outline: load all slide shapes + names in one shot.
      for (const sl of allSlides) {
        sl.shapes.load("items/name");
        sl.notesSlide.load("notesPlaceholder");
      }
      if (activeSlide) {
        activeSlide.shapes.load("items/name,items/textFrame/textRange/text");
        activeSlide.notesSlide.load("notesPlaceholder/textFrame/textRange/text");
        activeSlide.layout?.load("name");
      }
      await ctx.sync();

      // Resolve text per shape — we need a second sync to read text-range
      // contents on the non-active slides (cheap counts only).
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
          title,
          bullet_count: bulletCount,
          has_notes: !!(notesText && notesText.trim()),
        });
      }

      const deck_outline: DeckOutline = {
        slide_count: outlineSlides.length,
        slides: outlineSlides,
      };

      // Build current_slide payload.
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
        current_slide = {
          index: idx >= 0 ? idx : 0,
          title,
          bullets,
          notes: (notes || "").trim(),
          layout_name,
        };
      }

      return { current_slide, deck_outline };
    });
  } catch (e) {
    // Some PowerPoint clients only expose a subset of the JS API. Fail
    // soft — the backend will say "no slide" / "no deck".
    // eslint-disable-next-line no-console
    console.warn("snapshotCurrentContext failed:", e);
    return { current_slide: null, deck_outline: null };
  }
}

/** Replace the title, bullets, and speaker notes for the slide at `slideIndex`. */
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

    if (titleSh) titleSh.textFrame.textRange.load("text");
    if (contentSh && contentSh !== titleSh) contentSh.textFrame.textRange.load("text");
    await ctx.sync();

    if (titleSh) {
      try { titleSh.textFrame.textRange.text = title || ""; } catch { /* layout without editable title */ }
    }
    if (contentSh && contentSh !== titleSh) {
      const joined = (bullets || []).join("\n");
      try { contentSh.textFrame.textRange.text = joined; } catch { /* layout without editable body */ }
    }
    try {
      const np = sl.notesSlide?.notesPlaceholder;
      if (np) np.textFrame.textRange.text = notes || "";
    } catch {
      /* notes placeholder not available on every layout */
    }
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

    // PowerPoint JS API: addFromBase64 / add — add with no arguments
    // appends to the end. We then re-order it into place.
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
      try { newSlide.moveTo(targetIndex); } catch { /* best effort */ }
      await ctx.sync();
    }

    // Populate the new slide.
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

/** Reorder a slide from `fromIndex` to `toIndex`. Best-effort. */
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
