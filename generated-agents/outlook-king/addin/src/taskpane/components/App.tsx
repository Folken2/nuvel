import React, { useState, useRef, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  snapshotCurrentContext,
  snapshotAccount,
  OutlookContext,
  ComposeContext,
  ReadContext,
  insertIntoCompose,
  replaceCompose,
  executeOutlookAction,
} from "../helpers/outlookContext";
import {
  BACKEND_URL,
  apiHeaders,
  fetchWithRetry,
  readErrorMessage,
  waitForBackend,
  getSessionId,
} from "../../config/api";
import logger from "../../config/logger";
import "./App.css";

/* global Office */

interface ToolEvent {
  type: "tool_start" | "tool_end";
  tool: string;
  status: string;
  duration_ms?: number;
  args?: Record<string, string>;
  result_preview?: string;
}

type DraftAction = "insert" | "replace";

interface AppliedDraft {
  action: DraftAction;
  /** Whole compose body captured before mutation, used to restore on undo. */
  priorBody: string;
  draft: string;
}

interface ExecutedAction {
  type: string;
  status: "ok" | "error" | "skipped";
  description?: string;
  error?: string;
}

interface Message {
  role: "user" | "agent";
  content: string;
  toolEvents?: ToolEvent[];
  suggestions?: string[];
  /** Plain-text draft extracted from the reply (insertable into compose). */
  draft?: string | null;
  applied?: AppliedDraft;
  sourcePrompt?: string;
  traceId?: string;
  durationMs?: number;
  undone?: boolean;
  executedActions?: ExecutedAction[];
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

/* ── Tool display names ── */
const TOOL_DISPLAY_NAMES: Record<string, string> = {
  search_inbox: "Searching inbox",
  draft_reply: "Drafting reply",
  coach_draft: "Coaching draft",
  match_voice: "Matching voice",
  summarize_thread: "Summarizing thread",
  find_related: "Finding related emails",
  learn_sent: "Learning sent email",
  ask_user: "Asking question",
};

const POST_TOOL_MESSAGES: Record<string, string> = {
  search_inbox: "Reading results…",
  draft_reply: "Finalizing draft…",
  coach_draft: "Composing feedback…",
  summarize_thread: "Writing summary…",
};
const POST_TOOL_DEFAULT = "Putting it all together…";

const postToolMessage = (lastTool: string): string =>
  POST_TOOL_MESSAGES[lastTool] || POST_TOOL_DEFAULT;

const toolDisplayName = (name: string): string => {
  if (TOOL_DISPLAY_NAMES[name]) return TOOL_DISPLAY_NAMES[name];
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
};

/* ── Grouped tool-event helpers ── */

const ToolEventsGroup: React.FC<{ events: ToolEvent[] }> = ({ events }) => {
  const [expanded, setExpanded] = useState(false);

  if (events.length === 0) return null;

  const first = events[0];
  const rest = events.slice(1);
  const totalMs = events.reduce((s, e) => s + (e.duration_ms ?? 0), 0);
  const hiddenCount = rest.length;

  return (
    <div className="tool-events">
      <span className="tool-events-label">Tools</span>
      {first && (
        <div className={`tool-event tool-event-${first.status}`}>
          <span className="tool-event-icon">{first.status === "error" ? "✗" : "✓"}</span>
          <span className="tool-event-name">{toolDisplayName(first.tool)}</span>
          {first.duration_ms != null && <span className="tool-event-time">{first.duration_ms}ms</span>}
        </div>
      )}

      {hiddenCount > 0 && !expanded && (
        <button className="tool-group-toggle" onClick={() => setExpanded(true)}>
          + {hiddenCount} more
          <span className="tool-group-total">{totalMs}ms</span>
        </button>
      )}

      {expanded && (
        <>
          {rest.map((ev, j) => (
            <div key={j} className={`tool-event tool-event-${ev.status}`}>
              <span className="tool-event-icon">{ev.status === "error" ? "✗" : "✓"}</span>
              <span className="tool-event-name">{toolDisplayName(ev.tool)}</span>
              {ev.duration_ms != null && <span className="tool-event-time">{ev.duration_ms}ms</span>}
            </div>
          ))}
          <button className="tool-group-toggle" onClick={() => setExpanded(false)}>
            collapse
          </button>
        </>
      )}
    </div>
  );
};

const LiveEventsGroup: React.FC<{ events: ToolEvent[] }> = ({ events }) => {
  const [expanded, setExpanded] = useState(false);

  if (events.length === 0) {
    return (
      <div className="loading-dots">
        <span className="dot" />
        <span className="dot" />
        <span className="dot" />
      </div>
    );
  }

  const startedTools = new Set<string>();
  const endedTools = new Set<string>();
  for (const ev of events) {
    if (ev.type === "tool_start") startedTools.add(ev.tool);
    if (ev.type === "tool_end") endedTools.add(ev.tool);
  }
  const runningTools = [...startedTools].filter((t) => !endedTools.has(t));
  const completedEvents = events.filter((e) => e.type === "tool_end");
  const activeTool = runningTools[runningTools.length - 1] || null;

  return (
    <div className="live-tool-events">
      {activeTool ? (
        <div className="live-event live-event-tool_start">
          <span className="live-event-icon"><span className="spinner" /></span>
          <span className="live-event-name">{toolDisplayName(activeTool)}</span>
          {completedEvents.length > 0 && (
            <span className="live-event-status">+ {completedEvents.length} done</span>
          )}
        </div>
      ) : completedEvents.length > 0 ? (
        <div className="live-event live-event-tool_start">
          <span className="live-event-icon"><span className="spinner" /></span>
          <span className="live-event-name">{postToolMessage(completedEvents[completedEvents.length - 1].tool)}</span>
        </div>
      ) : null}

      {completedEvents.length > 0 && activeTool && (
        <>
          {!expanded && completedEvents.length > 0 && (
            <button className="tool-group-toggle tool-group-toggle-live" onClick={() => setExpanded(true)}>
              show {completedEvents.length} completed
            </button>
          )}
          {expanded && (
            <>
              {completedEvents.map((ev, j) => (
                <div key={j} className="live-event live-event-tool_end">
                  <span className="live-event-icon">{ev.status === "error" ? "✗" : "✓"}</span>
                  <span className="live-event-name">{toolDisplayName(ev.tool)}</span>
                  {ev.duration_ms != null && <span className="live-event-time">{ev.duration_ms}ms</span>}
                </div>
              ))}
              <button className="tool-group-toggle tool-group-toggle-live" onClick={() => setExpanded(false)}>
                collapse
              </button>
            </>
          )}
        </>
      )}
    </div>
  );
};

interface UserPreferences {
  name: string;
  role: string;
  company: string;
  language: string;
}

interface SavedConversation {
  sessionId: string;
  messages: Message[];
  timestamp: number;
  preview: string;
}

const HISTORY_KEY = "outlook_king_history";
const MAX_HISTORY = 3;

const loadHistory = (): SavedConversation[] => {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return [];
};

const saveToHistory = (sessionId: string, messages: Message[]) => {
  if (messages.length === 0) return;
  const firstUser = messages.find((m) => m.role === "user");
  const preview = firstUser?.content || "New conversation";
  const cleaned = messages.map((m) => {
    const { applied, ...rest } = m;
    return rest;
  });
  const entry: SavedConversation = {
    sessionId,
    messages: cleaned as Message[],
    timestamp: Date.now(),
    preview: preview.length > 60 ? preview.slice(0, 57) + "..." : preview,
  };
  const history = loadHistory().filter((h) => h.sessionId !== sessionId);
  history.unshift(entry);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, MAX_HISTORY)));
};

const formatTimeAgo = (ts: number): string => {
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  return `${days}d ago`;
};

const PREFS_KEY = "outlook_king_preferences";
const THEME_KEY = "outlook_king_theme";

const loadPreferences = (): UserPreferences => {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return { name: "", role: "", company: "", language: "English" };
};

const savePreferences = (prefs: UserPreferences) => {
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
};

/** Pull the first fenced block from the agent's reply if present;
 *  otherwise return the whole reply when it looks like a draft (starts with a
 *  greeting). */
function extractDraft(text: string): string | null {
  if (!text) return null;
  const fenced = text.match(/```(?:[\w-]*)?\n([\s\S]+?)\n```/);
  if (fenced) return fenced[1].trim();
  if (/^(hi|hey|hello|dear)\b/i.test(text.trim())) return text.trim();
  return null;
}

const App: React.FC = () => {
  const sessionIdRef = useRef(getSessionId());
  // Restore the prior chat for this stable sessionId so the taskpane survives
  // remounts (Outlook can rebuild the iframe even when the pane is pinned).
  const [messages, setMessages] = useState<Message[]>(() => {
    const prior = loadHistory().find((h) => h.sessionId === sessionIdRef.current);
    return prior?.messages ?? [];
  });
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [liveEvents, setLiveEvents] = useState<ToolEvent[]>([]);
  const userIdRef = useRef<string>(
    Office.context?.mailbox?.userProfile?.emailAddress || "outlook-user"
  );
  const [ctx, setCtx] = useState<OutlookContext>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const requestStartRef = useRef<number>(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [preferences, setPreferences] = useState<UserPreferences>(loadPreferences);
  const [backendStatus, setBackendStatus] = useState<"checking" | "ready" | "unreachable">("checking");

  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light") return saved;
    return "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    waitForBackend({
      onAttempt: () => { if (!cancelled) setBackendStatus("checking"); },
    }).then((ok) => { if (!cancelled) setBackendStatus(ok ? "ready" : "unreachable"); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const snap = await snapshotCurrentContext();
      if (!cancelled) setCtx(snap);
    };
    void refresh();
    try {
      Office.context.mailbox.addHandlerAsync(
        Office.EventType.ItemChanged,
        () => void refresh()
      );
    } catch { /* read-mode taskpanes don't always expose ItemChanged */ }
    const t = setInterval(() => {
      if (ctx?.mode === "compose") void refresh();
    }, 5000);
    return () => { cancelled = true; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updatePreference = (key: keyof UserPreferences, value: string) => {
    setPreferences((prev) => {
      const next = { ...prev, [key]: value };
      savePreferences(next);
      return next;
    });
  };

  useEffect(() => {
    if (messages.length > 0 && messages[messages.length - 1].role === "agent") {
      saveToHistory(sessionIdRef.current, messages);
    }
  }, [messages]);

  const startNewSession = () => {
    if (loading) return;
    saveToHistory(sessionIdRef.current, messages);
    sessionIdRef.current = crypto.randomUUID();
    setMessages([]);
    setInput("");
    setHistoryOpen(false);
  };

  const restoreConversation = (conv: SavedConversation) => {
    if (loading) return;
    saveToHistory(sessionIdRef.current, messages);
    sessionIdRef.current = conv.sessionId;
    setMessages(conv.messages);
    setInput("");
    setHistoryOpen(false);
  };

  useEffect(() => {
    if (!historyOpen) return;
    const handler = () => setHistoryOpen(false);
    const id = setTimeout(() => document.addEventListener("click", handler), 0);
    return () => { clearTimeout(id); document.removeEventListener("click", handler); };
  }, [historyOpen]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, liveEvents]);

  /* ── Action execution (agent → Outlook) ── */

  const executeAndReportAction = async (action: any): Promise<ExecutedAction> => {
    if (action?.type === "refresh_context") {
      try {
        const snap = await snapshotCurrentContext();
        setCtx(snap);
        const account = snapshotAccount();
        const body: any = {
          session_id: sessionIdRef.current,
          user_id: userIdRef.current,
          account,
        };
        if (snap?.mode === "compose") body.compose = snap;
        else if (snap?.mode === "read") body.selected = snap;
        await fetchWithRetry(`${BACKEND_URL}/api/outlook/context`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify(body),
        }, { maxRetries: 2, baseDelayMs: 1000 });
        await fetchWithRetry(`${BACKEND_URL}/api/outlook/action-result`, {
          method: "POST",
          headers: apiHeaders(),
          body: JSON.stringify({
            session_id: sessionIdRef.current,
            user_id: userIdRef.current,
            action_id: action.id,
            action_type: action.type,
            status: "ok",
          }),
        }, { maxRetries: 2, baseDelayMs: 1000 });
        return { type: action.type, status: "ok", description: action.description };
      } catch (e) {
        return {
          type: action.type,
          status: "error",
          description: action.description,
          error: e instanceof Error ? e.message : String(e),
        };
      }
    }

    const result = await executeOutlookAction(action);
    try {
      // This report is how the agent learns whether its action actually
      // landed — retry hard before giving up, or its model of the
      // mailbox silently drifts.
      await fetchWithRetry(`${BACKEND_URL}/api/outlook/action-result`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          user_id: userIdRef.current,
          action_id: action.id,
          action_type: action.type,
          status: result.status,
          error: result.error || "",
          detail: result.detail || {},
        }),
      }, { maxRetries: 3, baseDelayMs: 1000 });
    } catch (e) {
      logger.warn("action-result post failed after retries", { error: e instanceof Error ? e.message : String(e) });
    }
    return {
      type: action.type,
      status: result.status,
      description: action.description,
      error: result.error,
    };
  };

  /* ── Draft insert / replace / undo ── */

  const learnSent = async (draft: string) => {
    try {
      const snap = await snapshotCurrentContext();
      const recipient = snap?.mode === "compose" ? (snap as ComposeContext).to[0] || "" : "";
      const subject = snap?.mode === "compose" ? (snap as ComposeContext).subject || "" : "";
      await fetchWithRetry(`${BACKEND_URL}/api/outlook/learn-sent`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({ body: draft, recipient, subject }),
      }, { maxRetries: 2, baseDelayMs: 1000 });
    } catch (e) {
      logger.warn("learn-sent failed", { error: e instanceof Error ? e.message : String(e) });
    }
  };

  const handleInsert = async (msgIndex: number) => {
    const msg = messages[msgIndex];
    if (!msg.draft || msg.applied) return;
    try {
      const snap = await snapshotCurrentContext();
      const priorBody = snap?.mode === "compose" ? (snap as ComposeContext).body || "" : "";
      await insertIntoCompose(msg.draft, false);
      setMessages((prev) =>
        prev.map((m, i) =>
          i === msgIndex
            ? { ...m, applied: { action: "insert", priorBody, draft: msg.draft! } }
            : m
        )
      );
      void learnSent(msg.draft);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Insert failed");
    }
  };

  const handleReplace = async (msgIndex: number) => {
    const msg = messages[msgIndex];
    if (!msg.draft || msg.applied) return;
    try {
      const snap = await snapshotCurrentContext();
      const priorBody = snap?.mode === "compose" ? (snap as ComposeContext).body || "" : "";
      await replaceCompose(msg.draft, false);
      setMessages((prev) =>
        prev.map((m, i) =>
          i === msgIndex
            ? { ...m, applied: { action: "replace", priorBody, draft: msg.draft! } }
            : m
        )
      );
      void learnSent(msg.draft);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Replace failed");
    }
  };

  const handleUndo = async (msgIndex: number) => {
    const msg = messages[msgIndex];
    if (!msg.applied || msg.undone) return;
    try {
      await replaceCompose(msg.applied.priorBody, false);
      setMessages((prev) =>
        prev.map((m, i) => (i === msgIndex ? { ...m, undone: true } : m))
      );
    } catch {
      /* best-effort */
    }
  };

  const sendMessage = async (overridePrompt?: string) => {
    const prompt = (overridePrompt || input).trim();
    if (!prompt || loading) return;

    setInput("");
    setLoading(true);
    setLiveEvents([]);
    setMessages((prev) => [...prev, { role: "user", content: prompt }]);
    requestStartRef.current = Date.now();

    try {
      let snap: OutlookContext = null;
      try {
        snap = await snapshotCurrentContext();
        setCtx(snap);
        logger.info("sendMessage: context acquired", {
          mode: snap?.mode || "none",
        });
      } catch (ctxError) {
        logger.error("sendMessage: snapshotCurrentContext threw", {
          error: ctxError instanceof Error ? ctxError.message : String(ctxError),
        });
      }

      const body: any = {
        session_id: sessionIdRef.current,
        user_id: userIdRef.current,
        prompt,
        language: preferences.language || "English",
        account: snapshotAccount(),
      };
      if (snap?.mode === "compose") {
        const c = snap as ComposeContext;
        body.compose = {
          body: c.body,
          body_html: c.body_html,
          subject: c.subject,
          to: c.to,
          cc: c.cc,
          bcc: c.bcc,
          mode: c.composeMode,
          conversation_id: c.conversation_id,
          selection: c.selection,
          selection_is_html: c.selection_is_html,
          attachments: c.attachments,
          importance: c.importance,
        };
      } else if (snap?.mode === "read") {
        const r = snap as ReadContext;
        body.selected = {
          id: r.id,
          subject: r.subject,
          from: r.from,
          to: r.to,
          cc: r.cc,
          body: r.body,
          conversation_id: r.conversation_id,
          received: r.received,
          has_attachments: r.has_attachments,
          attachments: r.attachments,
          folder: r.folder,
          categories: r.categories,
          flag: r.flag,
        };
      }

      const progress = {
        finalText: "",
        collectedEvents: [] as ToolEvent[],
        queuedActions: [] as any[],
      };

      const streamOnce = async (): Promise<void> => {
        const response = await fetchWithRetry(`${BACKEND_URL}/api/outlook/chat/stream`, {
          method: "POST",
          headers: apiHeaders({ Accept: "text/event-stream" }),
          body: JSON.stringify(body),
        });

        if (!response.ok) {
          const detailMsg = await readErrorMessage(response);
          const err = new Error(detailMsg || `Backend error: ${response.status}`) as Error & {
            backendReported?: boolean;
          };
          if (detailMsg) err.backendReported = true;
          throw err;
        }
        if (backendStatus !== "ready") setBackendStatus("ready");

        const reader = response.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let buffer = "";
        const toolStartedAt: Record<string, number> = {};

        const STALL_TIMEOUT_MS = 360_000;

        while (true) {
          const readPromise = reader.read();
          const timeoutPromise = new Promise<never>((_, reject) => {
            const id = setTimeout(() => {
              reject(new Error("Connection to backend appears stalled. Please try again."));
            }, STALL_TIMEOUT_MS);
            readPromise.then(() => clearTimeout(id), () => clearTimeout(id));
          });

          let done: boolean;
          let value: Uint8Array | undefined;
          try {
            ({ done, value } = await Promise.race([readPromise, timeoutPromise]));
          } catch (streamError) {
            reader.cancel().catch(() => {});
            // A drop after the connection was established is retryable —
            // but not a stall, where the server may still be working.
            const e = streamError as Error & { midStream?: boolean };
            if (!(e?.message || "").includes("stalled")) e.midStream = true;
            throw e;
          }
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split("\n\n");
          buffer = blocks.pop() || "";

          for (const block of blocks) {
            const evMatch = block.match(/^event:\s*(\w+)\s*$/m);
            const dataMatch = block.match(/^data:\s*(.*)$/m);
            if (!evMatch || !dataMatch) continue;
            const evt = evMatch[1];
            let payload: any = {};
            try { payload = JSON.parse(dataMatch[1]); } catch { /* */ }

            if (evt === "tool_start") {
              const tool = payload.tool || "tool";
              toolStartedAt[tool] = Date.now();
              progress.collectedEvents.push({ type: "tool_start", tool, status: "running" });
              setLiveEvents([...progress.collectedEvents]);
            } else if (evt === "tool_end") {
              const tool = payload.tool || "tool";
              const startedAt = toolStartedAt[tool];
              const duration_ms = startedAt ? Date.now() - startedAt : undefined;
              const status: string = payload.status || (payload.error ? "error" : "ok");
              progress.collectedEvents.push({ type: "tool_end", tool, status, duration_ms });
              setLiveEvents([...progress.collectedEvents]);
            } else if (evt === "final") {
              progress.finalText = payload.text || "";
            } else if (evt === "action") {
              progress.queuedActions.push(payload);
            } else if (evt === "error") {
              const err = new Error(
                payload.message || "Backend error during streaming."
              ) as Error & { backendReported?: boolean };
              err.backendReported = true;
              throw err;
            }
          }
        }
      };

      // Auto-reconnect dropped streams, but only while the server has shown
      // no visible progress — re-sending after tool calls risks duplicate
      // side effects (e.g. the same action queued twice).
      const MAX_STREAM_ATTEMPTS = 3;
      for (let attempt = 1; ; attempt++) {
        try {
          await streamOnce();
          break;
        } catch (err) {
          const e = err as Error & { backendReported?: boolean; midStream?: boolean };
          const hasProgress =
            progress.collectedEvents.length > 0 ||
            !!progress.finalText ||
            progress.queuedActions.length > 0;
          if (e.midStream && !e.backendReported && !hasProgress && attempt < MAX_STREAM_ATTEMPTS) {
            logger.warn("stream dropped — reconnecting", { attempt, error: e.message });
            await new Promise((r) => setTimeout(r, 1000 * attempt));
            continue;
          }
          throw err;
        }
      }

      const executedActions: ExecutedAction[] = [];
      for (const act of progress.queuedActions) {
        const result = await executeAndReportAction(act);
        executedActions.push(result);
      }

      // After actions land, refresh the local context snapshot so subsequent
      // turns see the freshest state without waiting for a debounce tick.
      if (executedActions.length > 0) {
        try {
          const snap2 = await snapshotCurrentContext();
          setCtx(snap2);
        } catch {
          /* best-effort */
        }
      }

      const durationMs = Date.now() - requestStartRef.current;
      const agentMessage: Message = {
        role: "agent",
        content: progress.finalText || "Done.",
        toolEvents: progress.collectedEvents.filter((e) => e.type === "tool_end"),
        draft: extractDraft(progress.finalText),
        sourcePrompt: prompt,
        durationMs,
        executedActions: executedActions.length > 0 ? executedActions : undefined,
      };

      setMessages((prev) => [...prev, agentMessage]);
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      const backendReported = (error as { backendReported?: boolean } | null)?.backendReported;
      const isNetwork = errMsg.includes("Failed to fetch") || errMsg.includes("NetworkError") || errMsg.includes("ERR_CONNECTION") || errMsg.includes("Load failed");
      const isStall = errMsg.includes("stalled");
      const isColdStart = errMsg.includes("502") || errMsg.includes("503") || errMsg.includes("504");
      // backendReported messages are already user-safe (structured
      // {code, message} payloads from the backend) — show them as-is.
      const userMessage = backendReported
        ? errMsg
        : isNetwork || isColdStart
        ? "Could not reach the backend — it may still be warming up. Please wait a moment and try again."
        : isStall
        ? "The request timed out — the backend may be overloaded. Please try again."
        : `Something went wrong: ${errMsg}`;
      if (isNetwork || isColdStart) setBackendStatus("unreachable");
      logger.warn("sendMessage failed", { error: errMsg, backendReported: !!backendReported });
      // sourcePrompt gives the error bubble a Retry button.
      setMessages((prev) => [...prev, { role: "agent", content: userMessage, sourcePrompt: prompt }]);
    } finally {
      setLoading(false);
      setLiveEvents([]);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const mode: "compose" | "read" | "none" =
    ctx?.mode === "compose" ? "compose" : ctx?.mode === "read" ? "read" : "none";

  const SUGGESTIONS = SUGGESTIONS_BY_MODE[mode];
  const inputPlaceholder =
    mode === "compose"
      ? "Ask for a fix, a rewrite, or coaching…"
      : mode === "read"
      ? "Ask about this thread or draft a reply…"
      : "Message outlook-king…";

  const ctxLabel =
    ctx?.mode === "compose"
      ? `Compose · ${(ctx as ComposeContext).composeMode} · to ${(ctx as ComposeContext).to.join(", ") || "(no recipient)"}`
      : ctx?.mode === "read"
      ? `Reading · "${(ctx as ReadContext).subject.slice(0, 40)}" · from ${(ctx as ReadContext).from}`
      : "No active item";

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-logo">
          <svg width="24" height="24" viewBox="0 0 256 256" fill="none">
            <rect width="256" height="256" rx="56" fill="#1a1a1a" />
            <rect x="36" y="44" width="120" height="120" rx="28" fill="white" opacity="0.92" />
            <rect x="112" y="104" width="92" height="92" rx="22" fill="white" opacity="0.5" />
          </svg>
        </div>
        <span className="app-title">outlook-king</span>
        <button className="header-btn" onClick={startNewSession} disabled={loading} title="New conversation">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
          </svg>
        </button>
        <div className="header-btn-wrapper">
          <button className="header-btn" onClick={() => setHistoryOpen((v) => !v)} disabled={loading} title="History">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
            </svg>
          </button>
          {historyOpen && (
            <div className="history-dropdown">
              <div className="history-header">Recent conversations</div>
              {loadHistory().filter((h) => h.sessionId !== sessionIdRef.current).length === 0 ? (
                <div className="history-empty">No previous conversations</div>
              ) : (
                loadHistory()
                  .filter((h) => h.sessionId !== sessionIdRef.current)
                  .slice(0, MAX_HISTORY)
                  .map((conv) => (
                    <button
                      key={conv.sessionId}
                      className="history-item"
                      onClick={() => restoreConversation(conv)}
                    >
                      <span className="history-preview">{conv.preview}</span>
                      <span className="history-meta">
                        {conv.messages.length} msg{conv.messages.length !== 1 ? "s" : ""}
                        {" · "}
                        {formatTimeAgo(conv.timestamp)}
                      </span>
                    </button>
                  ))
              )}
            </div>
          )}
        </div>
        <button className="header-btn" onClick={() => setSettingsOpen(true)} title="Settings">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </header>

      {backendStatus === "checking" && (
        <div className="warmup-banner">
          <span className="warmup-spinner" />
          Warming up backend...
        </div>
      )}
      {backendStatus === "unreachable" && (
        <div className="warmup-banner warmup-error">
          Backend unreachable — requests may fail.
          <button
            className="warmup-retry"
            onClick={() => {
              setBackendStatus("checking");
              waitForBackend().then((ok) => setBackendStatus(ok ? "ready" : "unreachable"));
            }}
          >
            Retry
          </button>
        </div>
      )}

      <div className="messages">
        {messages.length === 0 && !loading && (
          <div className="welcome">
            <div className="welcome-icon">
              <svg width="28" height="28" viewBox="0 0 256 256" fill="none">
                <rect width="256" height="256" rx="56" fill="#1a1a1a" />
                <rect x="36" y="44" width="120" height="120" rx="28" fill="white" opacity="0.92" />
                <rect x="112" y="104" width="92" height="92" rx="22" fill="white" opacity="0.5" />
              </svg>
            </div>
            <p className="welcome-title">{preferences.name ? `Hi ${preferences.name}, what` : "What"} can I help with?</p>
            <p className="welcome-hint">
              I can find threads, draft replies in your voice, coach your current draft, and learn how you write.
            </p>
            <div className="welcome-suggestions">
              {SUGGESTIONS.map((s, i) => (
                <button key={i} className="suggestion" onClick={() => sendMessage(s)}>
                  <span className="suggestion-icon">{["T", "fx", "A", "☰"][i]}</span>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message message-${msg.role}`}>
            {msg.role === "agent" && (
              <div className="message-avatar">
                <svg width="16" height="16" viewBox="0 0 256 256" fill="none">
                  <rect width="256" height="256" rx="56" fill="#1a1a1a" />
                  <rect x="36" y="44" width="120" height="120" rx="28" fill="white" opacity="0.92" />
                  <rect x="112" y="104" width="92" height="92" rx="22" fill="white" opacity="0.5" />
                </svg>
              </div>
            )}
            <div className="message-bubble">
              {msg.role === "agent" && msg.toolEvents?.length ? (
                <div className="tool-section tool-section-top">
                  <ToolEventsGroup events={msg.toolEvents} />
                </div>
              ) : null}
              <div className="message-content">
                {msg.role === "agent" ? <Markdown remarkPlugins={[remarkGfm]}>{msg.content}</Markdown> : <p>{msg.content}</p>}
              </div>
              {msg.role === "agent" && msg.suggestions && msg.suggestions.length > 0 && !loading && i === messages.length - 1 && (
                <div className="message-suggestions">
                  {msg.suggestions.map((s, j) => (
                    <button key={j} className="message-suggestion" onClick={() => sendMessage(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )}
              {msg.durationMs != null && (
                <div className="message-duration">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
                  </svg>
                  {(() => {
                    const totalSec = Math.round(msg.durationMs! / 1000);
                    const min = Math.floor(totalSec / 60);
                    const sec = totalSec % 60;
                    return min > 0 ? `${min}m ${sec}s` : `${sec}s`;
                  })()}
                </div>
              )}
              {msg.role === "agent" && msg.executedActions && msg.executedActions.length > 0 && (
                <div className="executed-actions">
                  {msg.executedActions.map((a, j) => (
                    <div
                      key={j}
                      className={`executed-action executed-action-${a.status}`}
                      title={a.error || ""}
                    >
                      <span className="executed-action-icon">
                        {a.status === "ok" ? "✓" : a.status === "error" ? "✗" : "○"}
                      </span>
                      <span className="executed-action-label">
                        {a.description || a.type}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {msg.role === "agent" && (msg.draft || msg.applied || msg.sourcePrompt) ? (
                <div className="operation-actions">
                  {msg.draft && !msg.applied && mode === "compose" && (
                    <button
                      className="action-btn action-btn-retry"
                      onClick={() => handleInsert(i)}
                      disabled={loading}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
                      </svg>
                      Insert
                    </button>
                  )}
                  {msg.draft && !msg.applied && mode === "compose" && (
                    <button
                      className="action-btn action-btn-retry"
                      onClick={() => handleReplace(i)}
                      disabled={loading}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 0 1 4-4h14" />
                        <polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 0 1-4 4H3" />
                      </svg>
                      Replace
                    </button>
                  )}
                  {msg.applied && (
                    <button
                      className="action-btn action-btn-undo"
                      onClick={() => handleUndo(i)}
                      disabled={msg.undone || loading}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="1 4 1 10 7 10" />
                        <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                      </svg>
                      {msg.undone ? "Undone" : "Undo"}
                    </button>
                  )}
                  {msg.sourcePrompt && (
                    <button
                      className="action-btn action-btn-retry"
                      onClick={() => sendMessage(msg.sourcePrompt)}
                      disabled={loading}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="23 4 23 10 17 10" />
                        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
                      </svg>
                      Retry
                    </button>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        ))}

        {loading && (
          <div className="message message-agent">
            <div className="message-avatar">AI</div>
            <div className="message-bubble">
              <LiveEventsGroup events={liveEvents} />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="input-area">
        <div className="input-wrapper">
          <textarea
            className="input-field"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={inputPlaceholder}
            rows={1}
            disabled={loading}
          />
          <button className="send-button" onClick={() => sendMessage()} disabled={loading || !input.trim()}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="19" x2="12" y2="5" /><polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
        </div>
        <div className="input-hint">
          <span className="input-hint-text">{ctxLabel}</span>
          <span className="input-hint-kbd">Enter</span>
        </div>
      </div>

      {settingsOpen && (
        <div className="settings-overlay" onClick={() => setSettingsOpen(false)}>
          <div className="settings-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="settings-header">
              <h2 className="settings-title">Settings</h2>
              <button className="settings-close" onClick={() => setSettingsOpen(false)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            <div className="settings-section">
              <h3 className="settings-section-title">Appearance</h3>
              <div className="settings-row">
                <span className="settings-label">Theme</span>
                <div className="theme-toggle">
                  <button
                    className={`theme-option ${theme === "light" ? "theme-option-active" : ""}`}
                    onClick={() => setTheme("light")}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="5" />
                      <line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" />
                      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                      <line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" />
                      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                    </svg>
                    Light
                  </button>
                  <button
                    className={`theme-option ${theme === "dark" ? "theme-option-active" : ""}`}
                    onClick={() => setTheme("dark")}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                    </svg>
                    Dark
                  </button>
                </div>
              </div>
            </div>

            <div className="settings-section">
              <h3 className="settings-section-title">Language</h3>
              <p className="settings-section-hint">outlook-king will respond in this language.</p>
              <select
                className="settings-input"
                value={preferences.language || "English"}
                onChange={(e) => updatePreference("language", e.target.value)}
              >
                <option value="English">English</option>
                <option value="Spanish">Espa&#241;ol</option>
                <option value="Catalan">Catal&#224;</option>
                <option value="French">Fran&#231;ais</option>
                <option value="German">Deutsch</option>
                <option value="Italian">Italiano</option>
                <option value="Portuguese">Portugu&#234;s</option>
                <option value="Dutch">Nederlands</option>
                <option value="Japanese">&#26085;&#26412;&#35486;</option>
                <option value="Chinese">&#20013;&#25991;</option>
                <option value="Korean">&#54620;&#44397;&#50612;</option>
              </select>
            </div>

            <div className="settings-section">
              <h3 className="settings-section-title">Profile</h3>
              <p className="settings-section-hint">Helps outlook-king personalize responses.</p>
              <div className="settings-field">
                <label className="settings-field-label">Name</label>
                <input
                  className="settings-input"
                  type="text"
                  value={preferences.name}
                  onChange={(e) => updatePreference("name", e.target.value)}
                  placeholder="Your name"
                />
              </div>
              <div className="settings-field">
                <label className="settings-field-label">Role</label>
                <input
                  className="settings-input"
                  type="text"
                  value={preferences.role}
                  onChange={(e) => updatePreference("role", e.target.value)}
                  placeholder="e.g. Founder, BD, Recruiter"
                />
              </div>
              <div className="settings-field">
                <label className="settings-field-label">Company</label>
                <input
                  className="settings-input"
                  type="text"
                  value={preferences.company}
                  onChange={(e) => updatePreference("company", e.target.value)}
                  placeholder="Your company"
                />
              </div>
            </div>

            <div className="settings-footer">
              <span className="settings-version">outlook-king v0.1</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default App;
