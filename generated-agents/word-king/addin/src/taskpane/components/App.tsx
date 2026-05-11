import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BACKEND_URL, apiHeaders, getSessionId } from "../../config/api";
import {
  snapshotCurrentContext,
  WordContext,
  insertAtSelection,
  replaceSelection,
} from "../helpers/wordContext";
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

const SUGGESTIONS_BY_MODE: Record<"selection" | "document" | "none", string[]> = {
  selection: [
    "Rewrite my selection clearer",
    "Make this match my voice",
    "Tighten this — keep the meaning",
    "Fix the typo, nothing else",
  ],
  document: [
    "Draft a section about…",
    "Continue from the cursor",
    "Write the intro paragraph",
    "Make this match my voice",
  ],
  none: [
    "Draft a section about…",
    "Open a doc and select text to rewrite",
    "Study this paragraph: …",
    "What's my voice rulebook?",
  ],
};

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [ctx, setCtx] = useState<WordContext | null>(null);
  const [lastDraft, setLastDraft] = useState<string | null>(null);
  const sessionId = useMemo(() => getSessionId(), []);
  const userId = useMemo(
    () => (typeof Office !== "undefined" && Office.context?.document?.url) || "word-user",
    []
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  /* ── Context sync ────────────────────────────────────────── */

  const pushContext = useCallback(
    async (snap: WordContext | null) => {
      if (!snap) return;
      try {
        const body: any = {
          session_id: sessionId,
          user_id: userId,
          selection: snap.selection,
          document: snap.document,
        };
        await fetch(`${BACKEND_URL}/api/word/context`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify(body),
        });
      } catch (e) {
        // soft-fail: agent will say "no selection / no document" if state never arrived
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

    // Word doesn't expose a stable cross-host selectionChanged event we
    // can lean on, so we poll every 3s when the taskpane is visible.
    // Cheap (text reads are local) and avoids stale context.
    const t = setInterval(() => {
      void refreshContext();
    }, 3000);
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

      // Always send a fresh snapshot of the current Word state.
      const snap = await snapshotCurrentContext();
      setCtx(snap);

      const body: any = { session_id: sessionId, user_id: userId, prompt: trimmed };
      if (snap) {
        body.selection = snap.selection;
        body.document = snap.document;
      }

      try {
        const res = await fetch(`${BACKEND_URL}/api/word/chat/stream`, {
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
              setLastDraft(extractDraft(txt));
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

  /* ── Insert / replace / learn helpers ────────────────────── */

  const learnFromPassage = useCallback(
    async (passage: string, source: string) => {
      if (!passage.trim()) return;
      try {
        await fetch(`${BACKEND_URL}/api/word/learn-passage`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify({ passage, source, note: "" }),
        });
      } catch (e) {
        console.warn("learn-passage failed:", e);
      }
    },
    []
  );

  const onInsert = useCallback(async () => {
    if (!lastDraft) return;
    try {
      await insertAtSelection(lastDraft);
      void learnFromPassage(lastDraft, "accepted-draft");
    } catch (e: any) {
      alert(e?.message || "Insert failed");
    }
  }, [lastDraft, learnFromPassage]);

  const onReplace = useCallback(async () => {
    if (!lastDraft) return;
    try {
      await replaceSelection(lastDraft);
      void learnFromPassage(lastDraft, "accepted-draft");
    } catch (e: any) {
      alert(e?.message || "Replace failed");
    }
  }, [lastDraft, learnFromPassage]);

  /* ── Render ──────────────────────────────────────────────── */

  const mode: "selection" | "document" | "none" =
    ctx?.selection?.text?.trim()
      ? "selection"
      : ctx?.document?.text?.trim()
      ? "document"
      : "none";

  const ctxLabel = ctx
    ? mode === "selection"
      ? `Selection · ${ctx.selection.word_count} words · ${ctx.selection.paragraph_count} paragraph(s)`
      : mode === "document"
      ? `Document · ${ctx.document.word_count} words · ${ctx.document.paragraph_count} paragraph(s)`
      : "Empty document"
    : "Not connected to Word";

  return (
    <div className="app">
      <div className="header">
        <h1>word-king</h1>
        <div className="sub">Draft · Rewrite · Match your voice</div>
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
            <div className="role">word-king</div>
            <div>
              I'm wired into your document. Ask me to draft a section, rewrite
              your selection in your voice, or tighten what you have. Every
              passage you keep teaches me your voice.
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
            {m.role === "agent" && lastDraft && m === messages[messages.length - 1] && (
              <div className="insert-bar">
                <button onClick={onInsert}>Insert</button>
                <button onClick={onReplace} disabled={mode !== "selection"}>Replace selection</button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="input-row">
        <textarea
          value={input}
          placeholder={
            mode === "selection"
              ? "Ask for a rewrite, a fix, or a voice-match…"
              : "Ask for a draft, a section, or a continuation…"
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
 * Pull the first fenced block from the agent's reply if present;
 * otherwise return the whole reply when it looks like a draft (no
 * leading "Here's..." chatter, multiple paragraphs of prose).
 */
function extractDraft(text: string): string | null {
  if (!text) return null;
  const fenced = text.match(/```(?:[\w-]*)?\n([\s\S]+?)\n```/);
  if (fenced) return fenced[1].trim();
  const trimmed = text.trim();
  // Heuristic: drop a one-line preamble like "Here's the rewrite:" so the
  // Insert button doesn't carry it into the document.
  const preamble = /^(here'?s|here is|this is|drafted|rewrite)[^\n]{0,80}:\s*\n+/i;
  const stripped = trimmed.replace(preamble, "");
  // Treat anything ≥ 30 chars as draftable; the user gets to choose insert vs replace.
  return stripped.length >= 30 ? stripped : null;
}
