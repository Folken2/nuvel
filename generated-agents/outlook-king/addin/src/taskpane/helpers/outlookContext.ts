/**
 * Office.js shim for reading the user's current Outlook context.
 *
 * Two shapes:
 *   ReadContext     — the user is reading a message (item is read-only)
 *   ComposeContext  — the user is in a compose window (item is mutable)
 *
 * In compose mode, body/subject/to are async getters; we await them once
 * per snapshot. We do NOT subscribe to keystrokes here; the App component
 * polls or listens to ItemChanged / on compose body change events.
 */

/* global Office */

export interface ComposeContext {
  mode: "compose";
  composeMode: "newMail" | "reply" | "forward";
  body: string;
  subject: string;
  to: string[];
  cc: string[];
  conversation_id: string | null;
}

export interface ReadContext {
  mode: "read";
  id: string | null;
  subject: string;
  from: string;
  to: string[];
  body: string;
  conversation_id: string | null;
  received: string | null;
  has_attachments: boolean;
}

export type OutlookContext = ComposeContext | ReadContext | null;

function getBodyAsync(item: Office.Item): Promise<string> {
  return new Promise((resolve) => {
    if (!("body" in item) || !item.body) return resolve("");
    item.body.getAsync(Office.CoercionType.Text, (res) => {
      resolve(res.status === Office.AsyncResultStatus.Succeeded ? res.value : "");
    });
  });
}

function getRecipientsAsync(rec: Office.Recipients | undefined): Promise<string[]> {
  return new Promise((resolve) => {
    if (!rec) return resolve([]);
    rec.getAsync((res) => {
      resolve(
        res.status === Office.AsyncResultStatus.Succeeded
          ? (res.value || []).map((r) => r.emailAddress).filter(Boolean)
          : []
      );
    });
  });
}

function getSubjectAsync(item: Office.Item): Promise<string> {
  return new Promise((resolve) => {
    const s: any = (item as any).subject;
    if (typeof s === "string") return resolve(s);
    if (s && typeof s.getAsync === "function") {
      s.getAsync((res: Office.AsyncResult<string>) =>
        resolve(res.status === Office.AsyncResultStatus.Succeeded ? res.value || "" : "")
      );
    } else {
      resolve("");
    }
  });
}

function detectComposeMode(item: any): "newMail" | "reply" | "forward" {
  // Office.js doesn't expose the reply/forward distinction directly. We
  // approximate via conversationId presence + subject prefix.
  const subj = typeof item.subject === "string" ? item.subject : "";
  if (/^re:/i.test(subj)) return "reply";
  if (/^fwd?:/i.test(subj)) return "forward";
  return item.conversationId ? "reply" : "newMail";
}

export async function snapshotCurrentContext(): Promise<OutlookContext> {
  const item: any = Office.context?.mailbox?.item;
  if (!item) return null;

  const isCompose = !!(item.body && typeof item.body.setAsync === "function");

  if (isCompose) {
    const [body, subject, to, cc] = await Promise.all([
      getBodyAsync(item),
      getSubjectAsync(item),
      getRecipientsAsync(item.to),
      getRecipientsAsync(item.cc),
    ]);
    return {
      mode: "compose",
      composeMode: detectComposeMode(item),
      body,
      subject,
      to,
      cc,
      conversation_id: item.conversationId || null,
    };
  }

  const body = await getBodyAsync(item);
  return {
    mode: "read",
    id: item.itemId || null,
    subject: item.subject || "",
    from: item.from?.emailAddress || item.sender?.emailAddress || "",
    to: (item.to || []).map((r: any) => r.emailAddress).filter(Boolean),
    body,
    conversation_id: item.conversationId || null,
    received: item.dateTimeCreated ? new Date(item.dateTimeCreated).toISOString() : null,
    has_attachments: Array.isArray(item.attachments) && item.attachments.length > 0,
  };
}

/** Insert text/HTML into the active compose body. Replaces selection if any. */
export function insertIntoCompose(text: string, asHtml: boolean = false): Promise<void> {
  return new Promise((resolve, reject) => {
    const item: any = Office.context?.mailbox?.item;
    if (!item?.body?.setSelectedDataAsync) {
      return reject(new Error("Not in a compose window."));
    }
    item.body.setSelectedDataAsync(
      text,
      { coercionType: asHtml ? Office.CoercionType.Html : Office.CoercionType.Text },
      (res: Office.AsyncResult<void>) => {
        if (res.status === Office.AsyncResultStatus.Succeeded) resolve();
        else reject(new Error(res.error?.message || "Failed to insert"));
      }
    );
  });
}

/** Replace the entire compose body. */
export function replaceCompose(text: string, asHtml: boolean = false): Promise<void> {
  return new Promise((resolve, reject) => {
    const item: any = Office.context?.mailbox?.item;
    if (!item?.body?.setAsync) return reject(new Error("Not in a compose window."));
    item.body.setAsync(
      text,
      { coercionType: asHtml ? Office.CoercionType.Html : Office.CoercionType.Text },
      (res: Office.AsyncResult<void>) => {
        if (res.status === Office.AsyncResultStatus.Succeeded) resolve();
        else reject(new Error(res.error?.message || "Failed to replace"));
      }
    );
  });
}
