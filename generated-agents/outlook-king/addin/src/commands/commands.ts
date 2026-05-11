/**
 * Ribbon command handlers for outlook-king.
 *
 * Registered via manifest.xml's ExtensionPoints (FunctionName tag).
 * Each function must call event.completed() before returning so Outlook
 * unfreezes the ribbon.
 */

import { BACKEND_URL, apiHeaders, getSessionId } from "../config/api";

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

    const res = await fetch(`${BACKEND_URL}/api/outlook/chat`, {
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
    });

    if (!res.ok) {
      notify("error", `Coach failed: HTTP ${res.status}`);
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
