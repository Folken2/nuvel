---
name: outlook-search-via-composio
description: How to search the user's Outlook mailbox effectively using Composio's OUTLOOK_* tools, including query planning and result ranking
when_to_use: The user asks to find, look up, retrieve, or summarize a past email, attachment, or thread. Anything that isn't the currently-open compose or selected message.
---

# Outlook search via Composio

The agent does not call Microsoft Graph directly. The full mailbox is reachable through Composio's hosted MCP server, which exposes Outlook as a family of tools prefixed `OUTLOOK_*` (exact names vary by Composio version — discover them in the toolset, don't hardcode).

## The three-step search pattern

Every search request follows the same shape:

1. **Plan** — call `plan_email_search("<user's phrase>")`. Returns structured filters: `from_addresses`, `keywords`, `has_attachments`, `after_iso`, `before_iso`.
2. **Execute** — pick the right Composio Outlook tool:
   - Listing/filtering by sender or date window → `OUTLOOK_LIST_MESSAGES` (or whichever the toolset names it).
   - Full-text body+subject search → use the `$search` parameter where available.
   - Pull a specific thread → `OUTLOOK_GET_CONVERSATION` / `OUTLOOK_GET_MESSAGE`.
   - Drafts only → filter `isDraft eq true`.
3. **Rank** — pass the returned hits (as JSON) to `rank_search_hits`. Recent + frequent-sender hits float to the top.

## When to widen, when to narrow

- **Zero hits** → first widen the date window (drop `after_iso`), then drop keywords one at a time, then try `$search` instead of exact filters. Tell the user what you widened.
- **>20 hits** → narrow with stronger filters (specific sender, tighter date window, attachment requirement). Don't dump 20 results on the user — show the top 5 ranked, summarize the rest as "12 more".

## Common pitfalls

- The user says "last week" on a Monday — that usually means the previous Mon–Sun, not the past 7 days. If results from the literal 7-day window look thin, try the prior calendar week.
- Display names ≠ email addresses. If `plan_email_search` returns `from_addresses=[]` but the user named someone ("emails from Anna"), search by display name in `$search` instead of as a from-filter.
- Attachments in Outlook are not part of the message body by default. Use the attachment-specific tool to fetch them; the body field will not include their contents.

## Output shape

When you reply to the user with results, return:

- A one-line summary of what you found (count + what you filtered on)
- Up to 5 hits as a compact list: `[date] sender — subject (one-line snippet)`
- If you searched and got nothing, say what you tried and offer a wider variant.

Do not paste full email bodies unless the user asked to read one specifically.
