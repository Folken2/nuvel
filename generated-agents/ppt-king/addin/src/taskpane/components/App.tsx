import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BACKEND_URL, apiHeaders, getSessionId } from "../../config/api";
import {
  snapshotCurrentContext,
  setSlideContent,
  insertNewSlide,
  PptContext,
  CurrentSlide,
} from "../helpers/pptContext";
import "./App.css";

/* global Office */

interface ToolEvent {
  type: "tool_start" | "tool_end";
  tool: string;
}

interface Message {
  id: string;
  role: "user" | "agent";
  content: string;
  toolEvents?: ToolEvent[];
}

interface ParsedSlide {
  title: string;
  bullets: string[];
  notes: string;
}

const SUGGESTIONS_BY_MODE: Record<"slide" | "deck" | "none", string[]> = {
  slide: [
    "Tighten this slide",
    "Make bullets parallel",
    "Write speaker notes",
    "Strengthen the title",
  ],
  deck: [
    "Outline a deck about…",
    "Suggest a reorder",
    "What's missing?",
    "Where's the CTA?",
  ],
  none: [
    "Outline a 10-slide pitch about onboarding",
    "Outline a 15-minute training on code review",
    "Plan a weekly status update deck",
    "What makes a strong opening slide?",
  ],
};

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [ctx, setCtx] = useState<PptContext>({ current_slide: null, deck_outline: null });
  const [lastSlide, setLastSlide] = useState<ParsedSlide | null>(null);
  const sessionId = useMemo(() => getSessionId(), []);
  const userId = useMemo(
    () => (Office as any)?.context?.document?.url || "ppt-user",
    []
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  /* ── Context sync ────────────────────────────────────────── */

  const pushContext = useCallback(
    async (snap: PptContext) => {
      try {
        const body: any = { session_id: sessionId, user_id: userId };
        if (snap.current_slide) body.current_slide = snap.current_slide;
        if (snap.deck_outline) body.deck_outline = snap.deck_outline;
        await fetch(`${BACKEND_URL}/api/ppt/context`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify(body),
        });
      } catch (e) {
        console.warn("Context push failed:", e);
      }
    },
    [sessionId, userId]
  );

  const refreshContext = useCallback(async () => {
    const snap = await snapshotCurrentContext();
    setCtx(snap);
    await pushContext(snap);
  }, [pushContext]);

  useEffect(() => {
    refreshContext();
    // PowerPoint exposes selection-change via document.addHandlerAsync.
    const handler = () => void refreshContext();
    try {
      Office.context.document.addHandlerAsync(
        Office.EventType.DocumentSelectionChanged,
        handler
      );
    } catch {
      /* not every host supports the event */
    }
    // Light polling for slide-content edits (no granular event for those).
    const t = setInterval(() => {
      if (ctx?.current_slide) void refreshContext();
    }, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  /* ── Chat ────────────────────────────────────────────────── */

  const sendPrompt = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      const userMsg: Message = { id: `${Date.now()}-u`, role: "user", content: trimmed };
      const agentMsg: Message = { id: `${Date.now()}-a`, role: "agent", content: "", toolEvents: [] };
      setMessages((m) => [...m, userMsg, agentMsg]);
      setInput("");
      setSending(true);

      // Always send a fresh snapshot of the current PowerPoint state.
      const snap = await snapshotCurrentContext();
      setCtx(snap);

      const body: any = { session_id: sessionId, user_id: userId, prompt: trimmed };
      if (snap.current_slide) body.current_slide = snap.current_slide;
      if (snap.deck_outline) body.deck_outline = snap.deck_outline;

      try {
        const res = await fetch(`${BACKEND_URL}/api/ppt/chat/stream`, {
          method: "POST",
          headers: apiHeaders({ Accept: "text/event-stream" }),
          body: JSON.stringify(body),
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        let done = false;
        while (!done) {
          const { value, done: streamDone } = await reader.read();
          done = streamDone;
          if (value) buf += decoder.decode(value, { stream: true });

          const blocks = buf.split("\n\n");
          buf = blocks.pop() || "";
          for (const block of blocks) {
            const evMatch = block.match(/^event:\s*(\w+)\s*$/m);
            const dataMatch = block.match(/^data:\s*(.*)$/m);
            if (!evMatch || !dataMatch) continue;
            const evt = evMatch[1];
            let payload: any = {};
            try { payload = JSON.parse(dataMatch[1]); } catch { /* */ }

            if (evt === "tool_start" || evt === "tool_end") {
              setMessages((m) => {
                const copy = [...m];
                const last = copy[copy.length - 1];
                last.toolEvents = [...(last.toolEvents || []), { type: evt, tool: payload.tool }];
                return copy;
              });
            } else if (evt === "final") {
              const txt = payload.text || "";
              setLastSlide(extractSlide(txt));
              setMessages((m) => {
                const copy = [...m];
                copy[copy.length - 1].content = txt;
                return copy;
              });
            } else if (evt === "error") {
              setMessages((m) => {
                const copy = [...m];
                copy[copy.length - 1].content = `Error: ${payload.message || "unknown"}`;
                return copy;
              });
            }
          }
        }
      } catch (e: any) {
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1].content = `Error: ${e?.message || e}`;
          return copy;
        });
      } finally {
        setSending(false);
      }
    },
    [sending, sessionId, userId]
  );

  /* ── Apply / Insert helpers ──────────────────────────────── */

  const onApply = useCallback(async () => {
    if (!lastSlide || !ctx.current_slide) return;
    try {
      await setSlideContent(
        ctx.current_slide.index,
        lastSlide.title,
        lastSlide.bullets,
        lastSlide.notes
      );
      // Fire-and-forget learning signal — the user kept this slide.
      try {
        await fetch(`${BACKEND_URL}/api/ppt/learn-slide`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify({
            title: lastSlide.title,
            bullets: lastSlide.bullets,
            notes: lastSlide.notes,
            layout_name: ctx.current_slide.layout_name,
          }),
        });
      } catch { /* */ }
      await refreshContext();
    } catch (e: any) {
      alert(e?.message || "Apply failed");
    }
  }, [lastSlide, ctx.current_slide, refreshContext]);

  const onInsert = useCallback(async () => {
    if (!lastSlide) return;
    const after = ctx.current_slide?.index ?? (ctx.deck_outline?.slide_count ?? 1) - 1;
    try {
      await insertNewSlide(after, lastSlide.title, lastSlide.bullets, lastSlide.notes);
      try {
        await fetch(`${BACKEND_URL}/api/ppt/learn-slide`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify({
            title: lastSlide.title,
            bullets: lastSlide.bullets,
            notes: lastSlide.notes,
            layout_name: "",
          }),
        });
      } catch { /* */ }
      await refreshContext();
    } catch (e: any) {
      alert(e?.message || "Insert failed");
    }
  }, [lastSlide, ctx.current_slide, ctx.deck_outline, refreshContext]);

  /* ── Render ──────────────────────────────────────────────── */

  const mode: "slide" | "deck" | "none" =
    ctx.current_slide ? "slide" : ctx.deck_outline ? "deck" : "none";

  const ctxLabel = ctx.current_slide
    ? `Slide ${ctx.current_slide.index + 1}` +
      (ctx.current_slide.title ? ` — "${ctx.current_slide.title.slice(0, 60)}"` : "") +
      ` · ${ctx.current_slide.bullets.length} bullets` +
      ` · ${ctx.current_slide.notes ? "notes" : "no notes"}`
    : ctx.deck_outline
    ? `Deck · ${ctx.deck_outline.slide_count} slides`
    : "No deck open";

  return (
    <div className="app">
      <div className="header">
        <h1>ppt-king</h1>
        <div className="sub">Outline · Tighten · Restructure · Learn your slide style</div>
      </div>

      <div className="context-strip">
        <span className="label">Now:</span>
        {ctxLabel}
      </div>

      <div className="quick-row">
        {SUGGESTIONS_BY_MODE[mode].map((s) => (
          <button key={s} disabled={sending} onClick={() => sendPrompt(s)}>{s}</button>
        ))}
      </div>

      <div className="messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="msg agent">
            <div className="role">ppt-king</div>
            <div>
              I'm wired into your deck. Ask me to outline a new one, tighten the
              slide you're on, or suggest a reorder. Every slide you keep teaches
              me your slide style.
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <div className="role">{m.role}</div>
            {m.toolEvents && m.toolEvents.length > 0 && (
              <div>
                {m.toolEvents
                  .filter((t) => t.type === "tool_start")
                  .map((t, i) => (
                    <span key={i} className="tool-pill">{t.tool}</span>
                  ))}
              </div>
            )}
            <div>{m.content || (sending && m === messages[messages.length - 1] ? "…" : "")}</div>
            {m.role === "agent" && lastSlide && m === messages[messages.length - 1] && (
              <div className="insert-bar">
                {mode === "slide" && <button onClick={onApply}>Apply to this slide</button>}
                <button onClick={onInsert}>Insert as new slide</button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="input-row">
        <textarea
          value={input}
          placeholder={
            mode === "slide"
              ? "Ask for a tighten, parallelism fix, or speaker notes…"
              : mode === "deck"
              ? "Ask for a reorder, an outline, or what's missing…"
              : "Ask the king of PowerPoint…"
          }
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void sendPrompt(input);
            }
          }}
        />
        <button disabled={sending || !input.trim()} onClick={() => sendPrompt(input)}>
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

/**
 * Parse an agent reply for a structured slide block. The agent is asked
 * (by the system prompt) to format proposed slides as:
 *
 *   Title: <title>
 *   Bullets:
 *     - <bullet>
 *     - <bullet>
 *   Notes: <2-4 lines>
 *
 * Returns null when the reply doesn't look like a single proposed slide.
 */
function extractSlide(text: string): ParsedSlide | null {
  if (!text) return null;

  // Try the Title: / Bullets: / Notes: shape first.
  const titleMatch = text.match(/^\s*(?:Slide\s*\d+\s*[—-]\s*)?Title:\s*(.+?)\s*$/im);
  const bulletsMatch = text.match(/Bullets:\s*\n([\s\S]*?)(?:\n\s*Notes:|$)/i);
  const notesMatch = text.match(/Notes:\s*([\s\S]+?)\s*(?:\n\s*Slide\s*\d|$)/i);

  if (titleMatch && bulletsMatch) {
    const title = titleMatch[1].trim();
    const bullets = bulletsMatch[1]
      .split(/\r?\n/)
      .map((l) => l.replace(/^\s*[-*•]\s*/, "").trim())
      .filter((l) => l.length > 0);
    const notes = (notesMatch?.[1] || "").trim();
    if (bullets.length > 0) return { title, bullets, notes };
  }

  // Fallback: fenced block containing one slide.
  const fenced = text.match(/```(?:[\w-]*)?\n([\s\S]+?)\n```/);
  if (fenced) {
    const inner = fenced[1];
    const tm = inner.match(/Title:\s*(.+)/i);
    const bm = inner.match(/Bullets:\s*\n([\s\S]*?)(?:\nNotes:|$)/i);
    const nm = inner.match(/Notes:\s*([\s\S]+)/i);
    if (tm && bm) {
      const bullets = bm[1]
        .split(/\r?\n/)
        .map((l) => l.replace(/^\s*[-*•]\s*/, "").trim())
        .filter((l) => l.length > 0);
      if (bullets.length > 0) {
        return { title: tm[1].trim(), bullets, notes: (nm?.[1] || "").trim() };
      }
    }
  }

  return null;
}
