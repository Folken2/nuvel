/**
 * Ribbon command handlers for ppt-king.
 *
 * Registered via manifest.xml's ExtensionPoints (FunctionName tag).
 * Each function must call event.completed() before returning so PowerPoint
 * unfreezes the ribbon.
 */

import { BACKEND_URL, apiHeaders, getSessionId } from "../config/api";

/* global Office, PowerPoint */

Office.onReady(() => {
  // No-op: handlers below are registered by name via the manifest.
});

// ── Snapshot helpers ───────────────────────────────────────────────

interface CurrentSlideSnap {
  index: number;
  title: string;
  bullets: string[];
  notes: string;
  layout_name: string;
}

interface DeckSlideSnap {
  index: number;
  title: string;
  bullet_count: number;
  has_notes: boolean;
}

interface DeckSnap {
  slide_count: number;
  slides: DeckSlideSnap[];
}

function splitBullets(text: string): string[] {
  return (text || "")
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function readShapeText(shape: any): string {
  try { return shape?.textFrame?.textRange?.text || ""; } catch { return ""; }
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

async function readActiveSlide(): Promise<CurrentSlideSnap | null> {
  try {
    return await PowerPoint.run(async (ctx) => {
      const pres = ctx.presentation;
      const slides = pres.slides;
      slides.load("items");
      const selected = pres.getSelectedSlides();
      selected.load("items");
      await ctx.sync();
      const all = slides.items || [];
      const sl = (selected.items || [])[0] || all[0];
      if (!sl) return null;

      sl.shapes.load("items/name");
      sl.notesSlide.load("notesPlaceholder");
      sl.layout?.load("name");
      await ctx.sync();

      const shapes = sl.shapes.items || [];
      const titleSh = findTitleShape(shapes);
      const contentSh = findContentShape(shapes);
      if (titleSh) titleSh.textFrame.textRange.load("text");
      if (contentSh && contentSh !== titleSh) contentSh.textFrame.textRange.load("text");
      sl.notesSlide?.notesPlaceholder?.textFrame?.textRange?.load("text");
      await ctx.sync();

      const title = titleSh ? readShapeText(titleSh).trim() : "";
      const bulletText = contentSh && contentSh !== titleSh ? readShapeText(contentSh) : "";
      const bullets = splitBullets(bulletText);
      const notes = (sl.notesSlide?.notesPlaceholder?.textFrame?.textRange?.text || "").trim();
      const layout_name = sl.layout?.name || "";
      const idx = all.findIndex((x) => x.id === sl.id);
      return { index: idx >= 0 ? idx : 0, title, bullets, notes, layout_name };
    });
  } catch (e) {
    console.warn("readActiveSlide failed:", e);
    return null;
  }
}

async function readDeckOutline(): Promise<DeckSnap | null> {
  try {
    return await PowerPoint.run(async (ctx) => {
      const slides = ctx.presentation.slides;
      slides.load("items");
      await ctx.sync();
      const all = slides.items || [];
      for (const sl of all) {
        sl.shapes.load("items/name");
        sl.notesSlide.load("notesPlaceholder");
      }
      await ctx.sync();
      for (const sl of all) {
        const shapes = sl.shapes.items || [];
        const titleSh = findTitleShape(shapes);
        const contentSh = findContentShape(shapes);
        if (titleSh) titleSh.textFrame.textRange.load("text");
        if (contentSh && contentSh !== titleSh) contentSh.textFrame.textRange.load("text");
        sl.notesSlide?.notesPlaceholder?.textFrame?.textRange?.load("text");
      }
      await ctx.sync();

      const out: DeckSlideSnap[] = [];
      for (let i = 0; i < all.length; i++) {
        const sl = all[i];
        const shapes = sl.shapes.items || [];
        const titleSh = findTitleShape(shapes);
        const contentSh = findContentShape(shapes);
        const title = titleSh ? readShapeText(titleSh).trim() : "";
        const bulletText = contentSh && contentSh !== titleSh ? readShapeText(contentSh) : "";
        const bullets = splitBullets(bulletText);
        const notes = (sl.notesSlide?.notesPlaceholder?.textFrame?.textRange?.text || "").trim();
        out.push({
          index: i,
          title,
          bullet_count: bullets.length,
          has_notes: !!notes,
        });
      }
      return { slide_count: out.length, slides: out };
    });
  } catch (e) {
    console.warn("readDeckOutline failed:", e);
    return null;
  }
}

// ── Notifications ──────────────────────────────────────────────────

function notify(message: string) {
  // PowerPoint doesn't expose item-level notificationMessages like Outlook
  // does. We fall back to displayDialogAsync where available, then
  // console for headless logs.
  // eslint-disable-next-line no-console
  console.log("[ppt-king]", message);
  try {
    Office.context.ui.displayDialogAsync(
      // A data: URL means the dialog content lives inline — Office will
      // still open a small window with the message.
      `data:text/html,<html><body style="font-family:Segoe UI;padding:14px;">${encodeURIComponent(message)}</body></html>`,
      { height: 20, width: 30, displayInIframe: true },
      () => { /* fire-and-forget */ }
    );
  } catch {
    /* not all hosts support dialog API */
  }
}

// ── Ribbon handlers ────────────────────────────────────────────────

async function tightenCurrentSlide(event: Office.AddinCommands.Event) {
  try {
    const slide = await readActiveSlide();
    if (!slide || (!slide.title && slide.bullets.length === 0)) {
      notify("Open a slide with text first.");
      event.completed();
      return;
    }

    const res = await fetch(`${BACKEND_URL}/api/ppt/chat`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        session_id: getSessionId(),
        user_id: (Office as any)?.context?.document?.url || "ppt-user",
        prompt: "Tighten this slide. Apply the rubric.",
        current_slide: slide,
      }),
    });

    if (!res.ok) {
      notify(`Tighten failed: HTTP ${res.status}`);
    } else {
      const data = await res.json();
      const headline = (data.message || "")
        .split("\n")
        .find((l: string) => l.trim()) || "Open the taskpane for details.";
      notify(headline.slice(0, 200));
    }
  } catch (e: any) {
    notify(`Tighten failed: ${e?.message || e}`);
  } finally {
    event.completed();
  }
}

async function suggestReorder(event: Office.AddinCommands.Event) {
  try {
    const outline = await readDeckOutline();
    if (!outline || outline.slide_count < 3) {
      notify("Open a deck with at least 3 slides first.");
      event.completed();
      return;
    }

    const res = await fetch(`${BACKEND_URL}/api/ppt/chat`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        session_id: getSessionId(),
        user_id: (Office as any)?.context?.document?.url || "ppt-user",
        prompt: "Suggest reorderings if any earn their cost.",
        deck_outline: outline,
      }),
    });

    if (!res.ok) {
      notify(`Reorder failed: HTTP ${res.status}`);
    } else {
      const data = await res.json();
      const headline = (data.message || "")
        .split("\n")
        .find((l: string) => l.trim()) || "Open the taskpane for details.";
      notify(headline.slice(0, 200));
    }
  } catch (e: any) {
    notify(`Reorder failed: ${e?.message || e}`);
  } finally {
    event.completed();
  }
}

// Expose for the manifest's <FunctionName> hooks.
(window as any).tightenCurrentSlide = tightenCurrentSlide;
(window as any).suggestReorder = suggestReorder;
Office.actions.associate("tightenCurrentSlide", tightenCurrentSlide);
Office.actions.associate("suggestReorder", suggestReorder);
