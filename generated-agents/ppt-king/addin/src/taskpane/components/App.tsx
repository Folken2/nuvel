import React, { useState, useRef, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  snapshotCurrentContext,
  PptContext,
  setSlideContent,
  insertNewSlide,
  executeActionQueue,
  PptAction,
  ActionResult,
} from "../helpers/pptContext";
import {
  BACKEND_URL,
  apiHeaders,
  fetchWithRetry,
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

interface ParsedSlide {
  title: string;
  bullets: string[];
  notes: string;
}

interface AppliedSlide {
  action: "apply" | "insert";
  slideIndex: number;
  slide: ParsedSlide;
}

interface Message {
  role: "user" | "agent";
  content: string;
  toolEvents?: ToolEvent[];
  suggestions?: string[];
  /** Structured slide parsed from the reply (Title / Bullets / Notes). */
  slide?: ParsedSlide | null;
  applied?: AppliedSlide;
  sourcePrompt?: string;
  traceId?: string;
  durationMs?: number;
  undone?: boolean;
  /** Results of agent-queued PowerPoint actions executed this turn. */
  actionResults?: ActionResult[];
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

/* ── Tool display names ── */
const TOOL_DISPLAY_NAMES: Record<string, string> = {
  tighten_slide: "Tightening slide",
  outline_deck: "Outlining deck",
  suggest_reorder: "Suggesting reorder",
  speaker_notes: "Writing speaker notes",
  parallel_bullets: "Parallelizing bullets",
  learn_slide: "Learning slide style",
  ask_user: "Asking question",
};

const POST_TOOL_MESSAGES: Record<string, string> = {
  tighten_slide: "Polishing the slide…",
  outline_deck: "Assembling the outline…",
  suggest_reorder: "Sequencing the deck…",
  speaker_notes: "Drafting notes…",
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

const HISTORY_KEY = "ppt_king_history";
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

const PREFS_KEY = "ppt_king_preferences";
const THEME_KEY = "ppt_king_theme";

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

/** Parse a "Title: / Bullets: / Notes:" block out of the agent's reply. */
function extractSlide(text: string): ParsedSlide | null {
  if (!text) return null;
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

const App: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [liveEvents, setLiveEvents] = useState<ToolEvent[]>([]);
  const sessionIdRef = useRef(getSessionId());
  const userIdRef = useRef<string>(
    (typeof Office !== "undefined" && (Office.context as any)?.document?.url) || "ppt-user"
  );
  const [ctx, setCtx] = useState<PptContext>({ current_slide: null, deck_outline: null });
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
      Office.context.document.addHandlerAsync(
        Office.EventType.DocumentSelectionChanged,
        () => void refresh()
      );
    } catch { /* not every host supports the event */ }
    const t = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(t); };
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

  /* ── Slide apply / insert / undo ── */

  const learnSlide = async (slide: ParsedSlide, layoutName: string) => {
    try {
      await fetch(`${BACKEND_URL}/api/ppt/learn-slide`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({
          title: slide.title,
          bullets: slide.bullets,
          notes: slide.notes,
          layout_name: layoutName,
        }),
      });
    } catch (e) {
      logger.warn("learn-slide failed", { error: e instanceof Error ? e.message : String(e) });
    }
  };

  const handleApply = async (msgIndex: number) => {
    const msg = messages[msgIndex];
    if (!msg.slide || msg.applied || !ctx.current_slide) return;
    try {
      const slideIndex = ctx.current_slide.index;
      await setSlideContent(slideIndex, msg.slide.title, msg.slide.bullets, msg.slide.notes);
      setMessages((prev) =>
        prev.map((m, i) =>
          i === msgIndex
            ? { ...m, applied: { action: "apply", slideIndex, slide: msg.slide! } }
            : m
        )
      );
      void learnSlide(msg.slide, ctx.current_slide.layout_name);
      void fetch(`${BACKEND_URL}/api/ppt/record-edit`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          user_id: userIdRef.current,
          action: "apply_slide",
          slide_index: slideIndex,
          summary: `Applied "${msg.slide.title.slice(0, 40)}" to slide ${slideIndex + 1}`,
        }),
      }).catch(() => {});
      const snap = await snapshotCurrentContext();
      setCtx(snap);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Apply failed");
    }
  };

  const handleInsertSlide = async (msgIndex: number) => {
    const msg = messages[msgIndex];
    if (!msg.slide || msg.applied) return;
    const after = ctx.current_slide?.index ?? (ctx.deck_outline?.slide_count ?? 1) - 1;
    try {
      await insertNewSlide(after, msg.slide.title, msg.slide.bullets, msg.slide.notes);
      setMessages((prev) =>
        prev.map((m, i) =>
          i === msgIndex
            ? { ...m, applied: { action: "insert", slideIndex: after + 1, slide: msg.slide! } }
            : m
        )
      );
      void learnSlide(msg.slide, "");
      const snap = await snapshotCurrentContext();
      setCtx(snap);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Insert failed");
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
      let snap: PptContext = { current_slide: null, deck_outline: null };
      try {
        snap = await snapshotCurrentContext();
        setCtx(snap);
        logger.info("sendMessage: context acquired", {
          slideIndex: snap.current_slide?.index ?? -1,
          slideCount: snap.deck_outline?.slide_count ?? 0,
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
      };
      if (snap.current_slide) body.current_slide = snap.current_slide;
      if (snap.deck_outline) body.deck_outline = snap.deck_outline;

      const response = await fetchWithRetry(`${BACKEND_URL}/api/ppt/chat/stream`, {
        method: "POST",
        headers: apiHeaders({ Accept: "text/event-stream" }),
        body: JSON.stringify(body),
      });

      if (!response.ok) throw new Error(`Backend error: ${response.status}`);
      if (backendStatus !== "ready") setBackendStatus("ready");

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";
      let finalText = "";
      let pendingActions: PptAction[] = [];
      const collectedEvents: ToolEvent[] = [];
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
        } catch (stallError) {
          reader.cancel().catch(() => {});
          throw stallError;
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
            collectedEvents.push({ type: "tool_start", tool, status: "running" });
            setLiveEvents([...collectedEvents]);
          } else if (evt === "tool_end") {
            const tool = payload.tool || "tool";
            const startedAt = toolStartedAt[tool];
            const duration_ms = startedAt ? Date.now() - startedAt : undefined;
            const status: string = payload.status || (payload.error ? "error" : "ok");
            collectedEvents.push({ type: "tool_end", tool, status, duration_ms });
            setLiveEvents([...collectedEvents]);
          } else if (evt === "final") {
            finalText = payload.text || "";
          } else if (evt === "actions") {
            if (Array.isArray(payload.actions)) pendingActions = payload.actions;
          } else if (evt === "error") {
            throw new Error(payload.message || "Backend error during streaming.");
          }
        }
      }

      let actionResults: ActionResult[] | undefined;
      if (pendingActions.length > 0) {
        try {
          actionResults = await executeActionQueue(pendingActions, { continueOnError: true });
        } catch (e) {
          logger.warn("executeActionQueue threw", {
            error: e instanceof Error ? e.message : String(e),
          });
        }
        // Forward successful edits to the backend's recent-edits log.
        if (actionResults) {
          for (const r of actionResults) {
            if (r.status !== "ok") continue;
            try {
              await fetch(`${BACKEND_URL}/api/ppt/record-edit`, {
                method: "POST",
                headers: apiHeaders(),
                body: JSON.stringify({
                  session_id: sessionIdRef.current,
                  user_id: userIdRef.current,
                  action: r.type,
                  slide_index: r.slide_index ?? -1,
                  summary: r.summary || "",
                }),
              });
            } catch { /* best-effort */ }
          }
        }
        try {
          const snap = await snapshotCurrentContext();
          setCtx(snap);
        } catch { /* */ }
      }

      const durationMs = Date.now() - requestStartRef.current;
      const agentMessage: Message = {
        role: "agent",
        content: finalText || "Done.",
        toolEvents: collectedEvents.filter((e) => e.type === "tool_end"),
        slide: extractSlide(finalText),
        sourcePrompt: prompt,
        durationMs,
        actionResults,
      };

      setMessages((prev) => [...prev, agentMessage]);
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      const isNetwork = errMsg.includes("Failed to fetch") || errMsg.includes("NetworkError") || errMsg.includes("ERR_CONNECTION") || errMsg.includes("Load failed");
      const isStall = errMsg.includes("stalled");
      const isColdStart = errMsg.includes("502") || errMsg.includes("503") || errMsg.includes("504");
      const userMessage = isNetwork || isColdStart
        ? "Could not reach the backend — it may still be warming up. Please wait a moment and try again."
        : isStall
        ? "The request timed out — the backend may be overloaded. Please try again."
        : `Something went wrong: ${errMsg}`;
      if (isNetwork || isColdStart) setBackendStatus("unreachable");
      setMessages((prev) => [...prev, { role: "agent", content: userMessage }]);
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

  const mode: "slide" | "deck" | "none" =
    ctx.current_slide ? "slide" : ctx.deck_outline ? "deck" : "none";

  const SUGGESTIONS = SUGGESTIONS_BY_MODE[mode];
  const inputPlaceholder =
    mode === "slide"
      ? "Ask for a tighten, parallelism fix, or speaker notes…"
      : mode === "deck"
      ? "Ask for a reorder, an outline, or what's missing…"
      : "Message ppt-king…";

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
        <span className="app-title">ppt-king</span>
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
              I can outline a deck, tighten the active slide, suggest a reorder, and learn your slide style.
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
              {msg.role === "agent" && msg.actionResults && msg.actionResults.length > 0 && (
                <div className="action-results">
                  <span className="tool-events-label">Applied to PowerPoint</span>
                  {msg.actionResults.map((r, k) => (
                    <div key={k} className={`tool-event tool-event-${r.status === "ok" ? "ok" : r.status === "skip" ? "ok" : "error"}`}>
                      <span className="tool-event-icon">{r.status === "ok" ? "✓" : r.status === "skip" ? "○" : "✗"}</span>
                      <span className="tool-event-name">{r.summary || r.type}</span>
                      {r.message && <span className="tool-event-time">{r.message}</span>}
                    </div>
                  ))}
                </div>
              )}
              {msg.role === "agent" && (msg.slide || msg.applied || msg.sourcePrompt) ? (
                <div className="operation-actions">
                  {msg.slide && !msg.applied && mode === "slide" && (
                    <button
                      className="action-btn action-btn-retry"
                      onClick={() => handleApply(i)}
                      disabled={loading}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      Apply to this slide
                    </button>
                  )}
                  {msg.slide && !msg.applied && (
                    <button
                      className="action-btn action-btn-retry"
                      onClick={() => handleInsertSlide(i)}
                      disabled={loading}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
                      </svg>
                      Insert as new slide
                    </button>
                  )}
                  {msg.applied && (
                    <span className="action-btn" style={{ cursor: "default" }}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      {msg.applied.action === "apply" ? "Applied" : "Inserted"}
                    </span>
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
          <span className="input-hint-text">
            {ctx.current_slide
              ? `Slide ${ctx.current_slide.index + 1} · ${ctx.current_slide.bullets.length} bullets`
              : ctx.deck_outline
              ? `Deck · ${ctx.deck_outline.slide_count} slides`
              : "No deck open"}
          </span>
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
              <p className="settings-section-hint">ppt-king will respond in this language.</p>
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
              <p className="settings-section-hint">Helps ppt-king personalize responses.</p>
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
                  placeholder="e.g. PM, Designer, Founder"
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
              <span className="settings-version">ppt-king v0.1</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default App;
