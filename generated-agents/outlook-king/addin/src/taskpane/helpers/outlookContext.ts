/**
 * Office.js bridge for outlook-king.
 *
 * Two halves:
 *   - SNAPSHOT — read the user's current Outlook view (compose vs read,
 *     body, subject, recipients, selection, attachments, categories…)
 *     and ship it to the backend as session state.
 *   - EXECUTE — receive an action payload from the agent and apply it
 *     against the live Mailbox.Item (insert, set subject, add recipient,
 *     reply, attach, categorize, flag, etc.).
 *
 * Snapshot stays Promise-based and tolerant — Office.js surfaces vary
 * between Outlook desktop/web/Mac and between read/compose. We probe
 * capabilities ("does this method exist?") rather than relying on a
 * specific requirement set.
 */

/* global Office */

export interface AttachmentInfo {
  name: string;
  size: number;
  content_type: string;
  is_inline: boolean;
  id: string | null;
}

export interface ComposeContext {
  mode: "compose";
  composeMode: "newMail" | "reply" | "forward";
  body: string;
  body_html: string;
  subject: string;
  to: string[];
  cc: string[];
  bcc: string[];
  conversation_id: string | null;
  selection: string;
  selection_is_html: boolean;
  attachments: AttachmentInfo[];
  importance: "low" | "normal" | "high";
}

export interface ReadContext {
  mode: "read";
  id: string | null;
  subject: string;
  from: string;
  to: string[];
  cc: string[];
  body: string;
  conversation_id: string | null;
  received: string | null;
  has_attachments: boolean;
  attachments: AttachmentInfo[];
  folder: string;
  categories: string[];
  flag: "none" | "flagged" | "complete";
}

export interface AccountInfo {
  email: string;
  display_name: string;
  time_zone: string;
  host: string;
  platform: string;
}

export type OutlookContext = ComposeContext | ReadContext | null;

/* ── async getter helpers ──────────────────────────────────────── */

function getBodyAsync(item: any, coercion: "text" | "html"): Promise<string> {
  return new Promise((resolve) => {
    if (!item?.body?.getAsync) return resolve("");
    const ct = coercion === "html" ? Office.CoercionType.Html : Office.CoercionType.Text;
    item.body.getAsync(ct, (res: Office.AsyncResult<string>) => {
      resolve(res.status === Office.AsyncResultStatus.Succeeded ? res.value || "" : "");
    });
  });
}

function getRecipientsAsync(rec: Office.Recipients | undefined): Promise<string[]> {
  return new Promise((resolve) => {
    if (!rec || typeof rec.getAsync !== "function") return resolve([]);
    rec.getAsync((res) => {
      resolve(
        res.status === Office.AsyncResultStatus.Succeeded
          ? (res.value || []).map((r) => r.emailAddress).filter(Boolean)
          : []
      );
    });
  });
}

function getSubjectAsync(item: any): Promise<string> {
  return new Promise((resolve) => {
    const s = item?.subject;
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

function getSelectedDataAsync(item: any): Promise<{ data: string; isHtml: boolean }> {
  return new Promise((resolve) => {
    if (!item?.getSelectedDataAsync) return resolve({ data: "", isHtml: false });
    item.getSelectedDataAsync(
      Office.CoercionType.Text,
      (res: Office.AsyncResult<{ data: string; sourceProperty: string }>) => {
        if (res.status === Office.AsyncResultStatus.Succeeded && res.value) {
          resolve({ data: res.value.data || "", isHtml: false });
        } else {
          resolve({ data: "", isHtml: false });
        }
      }
    );
  });
}

function getCategoriesAsync(item: any): Promise<string[]> {
  return new Promise((resolve) => {
    if (!item?.categories?.getAsync) return resolve([]);
    item.categories.getAsync((res: Office.AsyncResult<any[]>) => {
      if (res.status === Office.AsyncResultStatus.Succeeded && Array.isArray(res.value)) {
        resolve(res.value.map((c: any) => c.displayName).filter(Boolean));
      } else {
        resolve([]);
      }
    });
  });
}

function getAttachmentsAsync(item: any): Promise<AttachmentInfo[]> {
  return new Promise((resolve) => {
    // Read-mode: attachments is a sync array. Compose: getAttachmentsAsync.
    if (Array.isArray(item?.attachments)) {
      resolve(
        item.attachments.map((a: any) => ({
          name: a.name || "",
          size: a.size || 0,
          content_type: a.contentType || "",
          is_inline: !!a.isInline,
          id: a.id || null,
        }))
      );
      return;
    }
    if (item?.getAttachmentsAsync) {
      item.getAttachmentsAsync((res: Office.AsyncResult<any[]>) => {
        if (res.status === Office.AsyncResultStatus.Succeeded && Array.isArray(res.value)) {
          resolve(
            res.value.map((a: any) => ({
              name: a.name || "",
              size: a.size || 0,
              content_type: a.contentType || "",
              is_inline: !!a.isInline,
              id: a.id || null,
            }))
          );
        } else {
          resolve([]);
        }
      });
    } else {
      resolve([]);
    }
  });
}

function getImportanceAsync(item: any): Promise<"low" | "normal" | "high"> {
  return new Promise((resolve) => {
    const v = item?.importance;
    if (typeof v === "string") return resolve((v as any) || "normal");
    if (v?.getAsync) {
      v.getAsync((res: Office.AsyncResult<string>) =>
        resolve((res.status === Office.AsyncResultStatus.Succeeded && (res.value as any)) || "normal")
      );
    } else {
      resolve("normal");
    }
  });
}

function detectComposeMode(item: any): "newMail" | "reply" | "forward" {
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
    const [body, bodyHtml, subject, to, cc, bcc, selection, attachments, importance] = await Promise.all([
      getBodyAsync(item, "text"),
      getBodyAsync(item, "html"),
      getSubjectAsync(item),
      getRecipientsAsync(item.to),
      getRecipientsAsync(item.cc),
      getRecipientsAsync(item.bcc),
      getSelectedDataAsync(item),
      getAttachmentsAsync(item),
      getImportanceAsync(item),
    ]);
    return {
      mode: "compose",
      composeMode: detectComposeMode(item),
      body,
      body_html: bodyHtml,
      subject,
      to,
      cc,
      bcc,
      conversation_id: item.conversationId || null,
      selection: selection.data,
      selection_is_html: selection.isHtml,
      attachments,
      importance,
    };
  }

  const [body, attachments, categories] = await Promise.all([
    getBodyAsync(item, "text"),
    getAttachmentsAsync(item),
    getCategoriesAsync(item),
  ]);

  const folder =
    item?.displayReplyFormAsync && (item as any).parentFolderName
      ? (item as any).parentFolderName
      : "";

  let flag: "none" | "flagged" | "complete" = "none";
  try {
    const f = item?.flag?.flagStatus;
    if (typeof f === "string") flag = f as any;
  } catch {
    /* not all hosts expose flag */
  }

  return {
    mode: "read",
    id: item.itemId || null,
    subject: item.subject || "",
    from: item.from?.emailAddress || item.sender?.emailAddress || "",
    to: (item.to || []).map((r: any) => r.emailAddress).filter(Boolean),
    cc: (item.cc || []).map((r: any) => r.emailAddress).filter(Boolean),
    body,
    conversation_id: item.conversationId || null,
    received: item.dateTimeCreated ? new Date(item.dateTimeCreated).toISOString() : null,
    has_attachments: attachments.length > 0,
    attachments,
    folder,
    categories,
    flag,
  };
}

export function snapshotAccount(): AccountInfo {
  const mb: any = Office.context?.mailbox;
  const prof = mb?.userProfile || {};
  const diag: any = Office.context?.diagnostics || {};
  return {
    email: prof.emailAddress || "",
    display_name: prof.displayName || "",
    time_zone: prof.timeZone || "",
    host: diag.host || "",
    platform: diag.platform || "",
  };
}

/* ── action executors ──────────────────────────────────────────── */

function asyncify<T>(fn: (cb: (r: Office.AsyncResult<T>) => void) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    try {
      fn((res) => {
        if (res.status === Office.AsyncResultStatus.Succeeded) resolve(res.value);
        else reject(new Error(res.error?.message || "Office.js call failed"));
      });
    } catch (e) {
      reject(e instanceof Error ? e : new Error(String(e)));
    }
  });
}

/** Insert text/HTML into the active compose body. Replaces selection if any. */
export function insertIntoCompose(text: string, asHtml: boolean = false): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.body?.setSelectedDataAsync) return Promise.reject(new Error("Not in a compose window."));
  return asyncify<void>((cb) =>
    item.body.setSelectedDataAsync(
      text,
      { coercionType: asHtml ? Office.CoercionType.Html : Office.CoercionType.Text },
      cb
    )
  );
}

/** Replace the entire compose body. */
export function replaceCompose(text: string, asHtml: boolean = false): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.body?.setAsync) return Promise.reject(new Error("Not in a compose window."));
  return asyncify<void>((cb) =>
    item.body.setAsync(
      text,
      { coercionType: asHtml ? Office.CoercionType.Html : Office.CoercionType.Text },
      cb
    )
  );
}

export function setSubject(subject: string): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.subject?.setAsync) return Promise.reject(new Error("Cannot set subject in this context."));
  return asyncify<void>((cb) => item.subject.setAsync(subject, cb));
}

export function addRecipients(addresses: string[], field: "to" | "cc" | "bcc"): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  const target = item?.[field] || item?.cc; // bcc may be absent → fall back
  if (!target?.addAsync) return Promise.reject(new Error(`No ${field} field available.`));
  const payload = addresses.map((a) => ({ emailAddress: a, displayName: a }));
  return asyncify<void>((cb) => target.addAsync(payload, cb));
}

export async function removeRecipients(addresses: string[], field: "to" | "cc" | "bcc"): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  const target = item?.[field] || item?.cc;
  if (!target?.getAsync || !target?.setAsync) throw new Error(`No ${field} field available.`);
  const current = await new Promise<any[]>((resolve) => {
    target.getAsync((res: Office.AsyncResult<any[]>) =>
      resolve(res.status === Office.AsyncResultStatus.Succeeded ? res.value || [] : [])
    );
  });
  const drop = new Set(addresses.map((a) => a.toLowerCase()));
  const kept = current.filter((r) => !drop.has((r.emailAddress || "").toLowerCase()));
  return asyncify<void>((cb) => target.setAsync(kept, cb));
}

export function setImportance(level: "low" | "normal" | "high"): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  // Importance setter is exposed in Mailbox preview 1.13+; gracefully no-op.
  if (!item?.importance?.setAsync) return Promise.reject(new Error("Importance not settable in this Outlook version."));
  return asyncify<void>((cb) => item.importance.setAsync(level, cb));
}

export function applyCategories(categories: string[]): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.categories?.addAsync) return Promise.reject(new Error("Categories API unavailable."));
  return asyncify<void>((cb) => item.categories.addAsync(categories, cb));
}

export function setFlag(state: "none" | "flagged" | "complete"): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.flag?.setAsync) return Promise.reject(new Error("Flag API unavailable."));
  return asyncify<void>((cb) => item.flag.setAsync({ flagStatus: state }, cb));
}

export function createReply(body: string, replyAll: boolean, asHtml: boolean): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  const fn = replyAll ? item?.displayReplyAllFormAsync : item?.displayReplyFormAsync;
  if (!fn) return Promise.reject(new Error("Reply not available in this context."));
  const options = body
    ? asHtml
      ? { htmlBody: body }
      : { htmlBody: body.replace(/\n/g, "<br>") }
    : undefined;
  return new Promise((resolve, reject) => {
    try {
      fn.call(item, options, (res: Office.AsyncResult<any>) => {
        if (!res || res.status === Office.AsyncResultStatus.Succeeded) resolve();
        else reject(new Error(res.error?.message || "Reply failed"));
      });
    } catch (e) {
      // Some hosts support sync invocation
      try {
        fn.call(item, options);
        resolve();
      } catch {
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    }
  });
}

export function createForward(to: string[], body: string, asHtml: boolean): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.displayReplyFormAsync && !item?.displayMessageFormAsync) {
    return Promise.reject(new Error("Forward not available in this context."));
  }
  // Best-effort: open a new compose pre-filled. Office.js lacks a direct
  // "forward" API on Item; we mimic via displayNewMessageForm with the
  // forwarded body inserted by the user. If Mailbox 1.13+, prefer it.
  const mb: any = Office.context?.mailbox;
  if (mb?.displayNewMessageFormAsync) {
    const opts: any = {
      toRecipients: to,
      subject: "Fwd: " + (item.subject || ""),
      htmlBody: asHtml ? body : body.replace(/\n/g, "<br>"),
    };
    return asyncify<void>((cb) => mb.displayNewMessageFormAsync(opts, cb));
  }
  // Fallback: open a reply form. Not a true forward but keeps the flow alive.
  return createReply(body, false, asHtml);
}

export interface AttachmentContentResult {
  /** Base64 string for files; raw text for eml/iCalendar; a link for cloud attachments. */
  content: string;
  /** Office.MailboxEnums.AttachmentContentFormat: "base64" | "eml" | "iCalendar" | "url". */
  format: string;
}

/**
 * Download an attachment's content via getAttachmentContentAsync
 * (Mailbox 1.8+, read AND compose modes). File attachments come back
 * base64-encoded (25 MB pre-encoding cap enforced by Office); cloud
 * attachments return only a URL.
 */
export function getAttachmentContent(attachmentId: string): Promise<AttachmentContentResult> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.getAttachmentContentAsync) {
    return Promise.reject(
      new Error("Attachment download requires Outlook with Mailbox API 1.8 or later.")
    );
  }
  return asyncify<AttachmentContentResult>((cb) =>
    item.getAttachmentContentAsync(attachmentId, cb)
  );
}

export function attachFileFromUrl(url: string, name: string, isInline: boolean): Promise<void> {
  const item: any = Office.context?.mailbox?.item;
  if (!item?.addFileAttachmentAsync) return Promise.reject(new Error("Attachments unavailable."));
  return asyncify<void>((cb) =>
    item.addFileAttachmentAsync(url, name, { isInline }, cb)
  );
}

/** Outlook action dispatcher. Returns a structured result the caller posts back. */
export interface ActionExecResult {
  status: "ok" | "error" | "skipped";
  error?: string;
  detail?: Record<string, any>;
}

export async function executeOutlookAction(
  action: { type: string; params?: any }
): Promise<ActionExecResult> {
  const p = action.params || {};
  try {
    switch (action.type) {
      case "insert_text":
        await insertIntoCompose(p.text, !!p.as_html);
        return { status: "ok" };
      case "replace_body":
        await replaceCompose(p.body, !!p.as_html);
        return { status: "ok" };
      case "set_subject":
        await setSubject(p.subject || "");
        return { status: "ok" };
      case "add_recipients":
        await addRecipients(p.addresses || [], p.field || "to");
        return { status: "ok" };
      case "remove_recipients":
        await removeRecipients(p.addresses || [], p.field || "to");
        return { status: "ok" };
      case "set_importance":
        await setImportance(p.level || "normal");
        return { status: "ok" };
      case "apply_categories":
        await applyCategories(p.categories || []);
        return { status: "ok" };
      case "set_flag":
        await setFlag(p.state || "flagged");
        return { status: "ok" };
      case "create_reply":
        await createReply(p.body || "", !!p.reply_all, !!p.as_html);
        return { status: "ok" };
      case "create_forward":
        await createForward(p.to || [], p.body || "", !!p.as_html);
        return { status: "ok" };
      case "attach_file_url":
        await attachFileFromUrl(p.url, p.name, !!p.is_inline);
        return { status: "ok" };
      case "refresh_context":
        // The caller (App.tsx) handles this by re-snapshotting after the
        // turn and pushing to /api/outlook/context. No-op here.
        return { status: "ok", detail: { handled_by: "caller" } };
      case "fetch_attachment":
        // Handled by the caller (App.tsx) — needs backend upload.
        return { status: "ok", detail: { handled_by: "caller" } };
      default:
        return { status: "skipped", error: `Unknown action: ${action.type}` };
    }
  } catch (e) {
    return { status: "error", error: e instanceof Error ? e.message : String(e) };
  }
}
