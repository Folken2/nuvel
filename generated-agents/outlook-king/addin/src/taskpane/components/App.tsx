import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BACKEND_URL, apiHeaders, getSessionId } from "../../config/api";
import {
  snapshotCurrentContext,
  OutlookContext,
  ComposeContext,
  ReadContext,
  insertIntoCompose,
  replaceCompose,
} from "../helpers/outlookContext";
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

const SUGGESTIONS_BY_MODE: Record<"compose" | "read" | "none", string[]> = {
  compose: [
    "Coach my draft",
    "Tighten this — keep the meaning",
    "Make it sound like me",
    "Add the right sign-off",
  ],
  read: [
    "Summarize this thread",
    "Draft a reply in my voice",
    "Find related emails",
    "Who's been emailing me about this?",
  ],
  none: [
    "Find unanswered emails from last week",
    "Search for the Q3 budget thread",
    "What did Anna say about the contract?",
    "Show me drafts I never sent",
  ],
};

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [ctx, setCtx] = useState<OutlookContext>(null);
  const [lastDraft, setLastDraft] = useState<string | null>(null);
  const sessionId = useMemo(() => getSessionId(), []);
  const userId = useMemo(
    () => Office.context?.mailbox?.userProfile?.emailAddress || "outlook-user",
    []
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  /* ── Context sync ────────────────────────────────────────── */

  const pushContext = useCallback(
    async (snap: OutlookContext) => {
      if (!snap) return;
      try {
        const body: any = { session_id: sessionId, user_id: userId };
        if (snap.mode === "compose") {
          const c = snap as ComposeContext;
          body.compose = {
            body: c.body,
            subject: c.subject,
            to: c.to,
            cc: c.cc,
            mode: c.composeMode,
            conversation_id: c.conversation_id,
          };
        } else {
          const r = snap as ReadContext;
          body.selected = {
            id: r.id,
            subject: r.subject,
            from: r.from,
            to: r.to,
            body: r.body,
            conversation_id: r.conversation_id,
            received: r.received,
            has_attachments: r.has_attachments,
          };
        }
        await fetch(`${BACKEND_URL}/api/outlook/context`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify(body),
        });
      } catch (e) {
        // soft-fail: agent will say "no compose" if state never arrived
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

    // Listen for Outlook switching the active item.
    const handler = () => {
      void refreshContext();
    };
    try {
      Office.context.mailbox.addHandlerAsync(
        Office.EventType.ItemChanged,
        handler
      );
    } catch {
      /* read-mode taskpanes don't always expose ItemChanged */
    }
    // Light polling for compose-body edits (Office.js compose-body change
    // events are not universally supported across Outlook clients).
    const t = setInterval(() => {
      if (ctx?.mode === "compose") void refreshContext();
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

      // Always send a fresh snapshot of the current Outlook state.
      const snap = await snapshotCurrentContext();
      setCtx(snap);

      const body: any = { session_id: sessionId, user_id: userId, prompt: trimmed };
      if (snap?.mode === "compose") {
        const c = snap as ComposeContext;
        body.compose = {
          body: c.body, subject: c.subject, to: c.to, cc: c.cc,
          mode: c.composeMode, conversation_id: c.conversation_id,
        };
      } else if (snap?.mode === "read") {
        const r = snap as ReadContext;
        body.selected = {
          id: r.id, subject: r.subject, from: r.from, to: r.to, body: r.body,
          conversation_id: r.conversation_id, received: r.received,
          has_attachments: r.has_attachments,
        };
      }

      try {
        const res = await fetch(`${BACKEND_URL}/api/outlook/chat/stream`, {
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

  /* ── Insert / replace draft helpers ──────────────────────── */

  const onInsert = useCallback(async () => {
    if (!lastDraft) return;
    try {
      await insertIntoCompose(lastDraft, false);
    } catch (e: any) {
      alert(e?.message || "Insert failed");
    }
  }, [lastDraft]);

  const onReplace = useCallback(async () => {
    if (!lastDraft) return;
    try {
      await replaceCompose(lastDraft, false);
    } catch (e: any) {
      alert(e?.message || "Replace failed");
    }
  }, [lastDraft]);

  /* ── Render ──────────────────────────────────────────────── */

  const mode: "compose" | "read" | "none" =
    ctx?.mode === "compose" ? "compose" : ctx?.mode === "read" ? "read" : "none";

  const ctxLabel =
    ctx?.mode === "compose"
      ? `Compose · ${(ctx as ComposeContext).composeMode} · to ${(ctx as ComposeContext).to.join(", ") || "(no recipient)"}`
      : ctx?.mode === "read"
      ? `Reading · "${(ctx as ReadContext).subject.slice(0, 60)}" · from ${(ctx as ReadContext).from}`
      : "No active item";

  return (
    <div className="app">
      <div className="header">
        <h1>outlook-king</h1>
        <div className="sub">Search · Draft · Coach · Learn your voice</div>
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
            <div className="role">outlook-king</div>
            <div>
              I'm wired into your inbox. Ask me to find a thread, draft a reply,
              coach your current draft, or just chat. Every email you send teaches
              me your voice.
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
            {m.role === "agent" && lastDraft && m === messages[messages.length - 1] && mode === "compose" && (
              <div className="insert-bar">
                <button onClick={onInsert}>Insert</button>
                <button onClick={onReplace}>Replace</button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="input-row">
        <textarea
          value={input}
          placeholder={mode === "compose" ? "Ask for a fix, a rewrite, or coaching…" : "Ask the king of Outlook…"}
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

/** Pull the first fenced or quoted block from the agent's reply, else return whole. */
function extractDraft(text: string): string | null {
  if (!text) return null;
  const fenced = text.match(/```(?:[\w-]*)?\n([\s\S]+?)\n```/);
  if (fenced) return fenced[1].trim();
  // Heuristic: if reply starts with "Hi <name>," or "Hey", treat the whole thing.
  if (/^(hi|hey|hello|dear)\b/i.test(text.trim())) return text.trim();
  return null;
}
