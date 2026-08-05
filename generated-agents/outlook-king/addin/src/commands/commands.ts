/**
 * Ribbon command handlers for outlook-king.
 *
 * Registered via manifest.xml's ExtensionPoints (FunctionName tag).
 * Each function must call event.completed() before returning so Outlook
 * unfreezes the ribbon.
 */

import { BACKEND_URL, apiHeaders, fetchWithRetry, readErrorMessage, getSessionId } from "../config/api";
import logger from "../config/logger";

/* global Office */

Office.onReady(() => {
  // No-op: handlers below are registered by name via the manifest.
});

async function readComposeSnapshot(): Promise<{
  body: string;
  subject: string;
  to: string[];
  cc: string[];
} | null> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.body?.getAsync) return null;

  const getBody = (): Promise<string> =>
    new Promise((resolve) =>
      item.body.getAsync(Office.CoercionType.Text, (r: Office.AsyncResult<string>) =>
        resolve(r.status === Office.AsyncResultStatus.Succeeded ? r.value || "" : "")
      )
    );

  const getSubj = (): Promise<string> =>
    new Promise((resolve) => {
      const s: any = item.subject;
      if (typeof s === "string") return resolve(s);
      s.getAsync((r: Office.AsyncResult<string>) =>
        resolve(r.status === Office.AsyncResultStatus.Succeeded ? r.value || "" : "")
      );
    });

  const getRec = (rec: any): Promise<string[]> =>
    new Promise((resolve) => {
      if (!rec?.getAsync) return resolve([]);
      rec.getAsync((r: Office.AsyncResult<Office.EmailAddressDetails[]>) =>
        resolve(
          r.status === Office.AsyncResultStatus.Succeeded
            ? (r.value || []).map((x) => x.emailAddress).filter(Boolean)
            : []
        )
      );
    });

  const [body, subject, to, cc] = await Promise.all([getBody(), getSubj(), getRec(item.to), getRec(item.cc)]);
  return { body, subject, to, cc };
}

/**
 * Quick action: send the current draft to the agent for coaching and surface
 * a notification with the headline feedback. Full feedback still lands in
 * the taskpane (which the manifest's button opens alongside).
 */
async function coachCurrentDraft(event: Office.AddinCommands.Event) {
  try {
    const snap = await readComposeSnapshot();
    if (!snap || !snap.body.trim()) {
      notify("warning", "Open a draft with text first.");
      event.completed();
      return;
    }

    const res = await fetchWithRetry(`${BACKEND_URL}/api/outlook/chat`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        session_id: getSessionId(),
        user_id: Office.context?.mailbox?.userProfile?.emailAddress,
        prompt: "Coach this draft. Be specific, voice-aware, and short.",
        compose: {
          body: snap.body,
          subject: snap.subject,
          to: snap.to,
          cc: snap.cc,
          mode: "reply",
          conversation_id: (Office.context.mailbox.item as any)?.conversationId || null,
        },
      }),
    }, { maxRetries: 2, baseDelayMs: 1000 });

    if (!res.ok) {
      const detailMsg = await readErrorMessage(res);
      notify("error", `Coach failed: ${detailMsg || `HTTP ${res.status}`}`);
    } else {
      const data = await res.json();
      const headline = (data.message || "").split("\n").find((l: string) => l.trim()) || "Open the taskpane for details.";
      notify("informational", headline.slice(0, 150));
    }
  } catch (e: any) {
    notify("error", `Coach failed: ${e?.message || e}`);
  } finally {
    event.completed();
  }
}

function notify(type: "informational" | "warning" | "error", message: string) {
  try {
    Office.context.mailbox.item?.notificationMessages?.replaceAsync("outlook-king", {
      type:
        type === "error"
          ? Office.MailboxEnums.ItemNotificationMessageType.ErrorMessage
          : type === "warning"
          ? Office.MailboxEnums.ItemNotificationMessageType.InformationalMessage
          : Office.MailboxEnums.ItemNotificationMessageType.InformationalMessage,
      message,
      icon: "Icon.16x16",
      persistent: false,
    });
  } catch {
    /* notifications aren't available in every host */
  }
}

// Expose for the manifest's <FunctionName> hooks.
(window as any).coachCurrentDraft = coachCurrentDraft;
Office.actions.associate("coachCurrentDraft", coachCurrentDraft);

/* ──────────────────────────────────────────────────────────────────
 * JSON-manifest-only event handlers.
 *
 * These functions are wired via manifest.json's `autoRunEvents` and the
 * `spamPreProcessingDialog` ribbon surface. The XML manifest doesn't
 * declare any of them, so under the XML build they're inert — Outlook
 * never fires them.
 * ────────────────────────────────────────────────────────────────── */

const sessionUserId = (): string | undefined =>
  Office.context?.mailbox?.userProfile?.emailAddress;

async function postJson(path: string, body: unknown, timeoutMs = 4000): Promise<Response | null> {
  try {
    return await fetchWithRetry(`${BACKEND_URL}${path}`, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify(body),
    }, { maxRetries: 2, baseDelayMs: 500, timeoutMs });
  } catch (e) {
    logger.warn(`postJson ${path} failed after retries`, {
      error: e instanceof Error ? e.message : String(e),
    });
    void logger.flush();
    return null;
  }
}

async function readAttachmentMetadata(): Promise<{ name: string; size: number }[]> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.getAttachmentsAsync) return [];
  return new Promise((resolve) => {
    try {
      item.getAttachmentsAsync((r: any) => {
        if (r?.status !== Office.AsyncResultStatus.Succeeded) return resolve([]);
        resolve((r.value || []).map((a: any) => ({ name: a.name || "", size: a.size || 0 })));
      });
    } catch {
      resolve([]);
    }
  });
}

async function readComposeTypeIfAvailable(): Promise<string> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.getComposeTypeAsync) return "newMail";
  return new Promise((resolve) => {
    try {
      item.getComposeTypeAsync((r: any) => {
        if (r?.status !== Office.AsyncResultStatus.Succeeded) return resolve("newMail");
        // r.value is e.g. { composeType: "newMail" | "reply" | "forward", coercionType: ... }
        resolve(r.value?.composeType || "newMail");
      });
    } catch {
      resolve("newMail");
    }
  });
}

async function pushComposeOpened(composeType: string, event: Office.AddinCommands.Event) {
  try {
    const snap = await readComposeSnapshot();
    const attachments = await readAttachmentMetadata();
    await postJson("/api/outlook/compose-opened", {
      session_id: getSessionId(),
      user_id: sessionUserId(),
      compose_type: composeType,
      compose: {
        body: snap?.body || "",
        subject: snap?.subject || "",
        to: snap?.to || [],
        cc: snap?.cc || [],
        attachments,
        conversation_id: (Office.context.mailbox.item as any)?.conversationId || null,
      },
    });
  } catch (e) {
    // Fire-and-forget for UX, but never silent — the agent loses track of
    // open drafts when these pushes vanish without a trace.
    logger.warn("compose-opened push failed", {
      error: e instanceof Error ? e.message : String(e),
    });
    void logger.flush();
  } finally {
    event.completed();
  }
}

async function onNewMessageComposeHandler(event: Office.AddinCommands.Event) {
  // OnNewMessageCompose — fires for genuinely new compose windows.
  await pushComposeOpened("newMail", event);
}

async function onMessageComposeOpenedHandler(event: Office.AddinCommands.Event) {
  // OnMessageCompose — covers replies, forwards, and editing drafts.
  const composeType = await readComposeTypeIfAvailable();
  await pushComposeOpened(composeType, event);
}

async function onMessageSendHandler(event: Office.AddinCommands.Event) {
  // Smart Alerts on-send. Default to allowing send if anything goes wrong —
  // the backend must never become a hard send blocker if it's offline.
  try {
    const snap = await readComposeSnapshot();
    const attachments = await readAttachmentMetadata();
    const res = await postJson("/api/outlook/pre-send-check", {
      session_id: getSessionId(),
      user_id: sessionUserId(),
      compose: {
        body: snap?.body || "",
        subject: snap?.subject || "",
        to: snap?.to || [],
        cc: snap?.cc || [],
        attachments,
      },
    });

    if (!res || !res.ok) {
      logger.warn("pre-send check unavailable — allowing send unchecked", {
        status: res ? res.status : "network-error",
      });
      void logger.flush();
      (event as any).completed({ allowEvent: true });
      return;
    }
    const data: any = await res.json().catch(() => null);
    if (data && data.allow === false) {
      (event as any).completed({
        allowEvent: false,
        errorMessage: data.message || "outlook-king flagged this draft. Please review before sending.",
      });
      return;
    }
    (event as any).completed({ allowEvent: true });
  } catch (e) {
    logger.warn("pre-send check failed — allowing send unchecked", {
      error: e instanceof Error ? e.message : String(e),
    });
    void logger.flush();
    (event as any).completed({ allowEvent: true });
  }
}

async function onSpamReportHandler(event: any) {
  // Integrated spam reporting (Mailbox 1.14+). Best-effort POST then
  // signal completion with a thank-you dialog. We never fail closed.
  try {
    const item: any = Office.context?.mailbox?.item;
    const meta: Record<string, unknown> = {
      session_id: getSessionId(),
      user_id: sessionUserId(),
      message_id: item?.itemId || null,
      conversation_id: item?.conversationId || null,
      subject: typeof item?.subject === "string" ? item.subject : "",
      sender: item?.from?.emailAddress || item?.sender?.emailAddress || "",
      options: (event as any)?.options || null,
      free_text: (event as any)?.freeText || "",
    };
    await postJson("/api/outlook/report-spam", meta);
  } catch (e) {
    // Never let a network blip break the report flow — but leave a trace.
    logger.warn("spam-report push failed", {
      error: e instanceof Error ? e.message : String(e),
    });
    void logger.flush();
  } finally {
    try {
      event.completed({
        onErrorDeleteItem: false,
        moveItemTo: Office.MailboxEnums?.MoveSpamItemTo?.JunkFolder,
        showPostProcessingDialog: {
          title: "outlook-king",
          description: "Thanks — logged for the agent to review.",
        },
      });
    } catch {
      event.completed();
    }
  }
}

(window as any).onNewMessageComposeHandler = onNewMessageComposeHandler;
(window as any).onMessageComposeOpenedHandler = onMessageComposeOpenedHandler;
(window as any).onMessageSendHandler = onMessageSendHandler;
(window as any).onSpamReportHandler = onSpamReportHandler;

Office.actions.associate("onNewMessageComposeHandler", onNewMessageComposeHandler);
Office.actions.associate("onMessageComposeOpenedHandler", onMessageComposeOpenedHandler);
Office.actions.associate("onMessageSendHandler", onMessageSendHandler);
Office.actions.associate("onSpamReportHandler", onSpamReportHandler);
