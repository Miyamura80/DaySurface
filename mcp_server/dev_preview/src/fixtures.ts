// Fixture data for the MCP-UI dev preview. No Gmail, no OAuth, no network -
// these canned payloads stand in for the responses a real MCP server would
// return, so a committed app bundle can be rendered in isolation.
//
// Shapes mirror the app types (see mcp_server/apps/*/src/*.tsx). When a tool's
// contract changes, update the matching entry here.

type ToolResult = {
  content: { type: "text"; text: string }[];
  structuredContent: Record<string, unknown>;
};

export function ok(data: unknown): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(data) }],
    structuredContent: (data ?? {}) as Record<string, unknown>,
  };
}

// --- gmail_inbox ----------------------------------------------------------

const INBOX_CURATE = {
  threads: [
    {
      thread_id: "t-1001",
      subject: "Series A term sheet - final redlines",
      from: "Dana Whitfield <dana@northwind.vc>",
      snippet:
        "Great call today. Attaching the final redlines - one open point on the liquidation preference…",
      last_message_at: "2026-07-05T09:14:00Z",
      importance_score: 0.97,
      reasons: ["Unread", "VIP sender", "Awaiting reply"],
      labels: [
        { name: "Unread", bg_color: "#e8f0fe", text_color: "#1a73e8" },
        { name: "Finance", bg_color: "#e6f4ea", text_color: "#137333" },
      ],
      has_draft: false,
    },
    {
      thread_id: "t-1002",
      subject: "Re: Onsite interview loop for Staff Eng",
      from: "Priya Nair <priya@peoplehq.io>",
      snippet:
        "Confirming Thursday 10am–2pm. I've looped in the panel. Let me know if the schedule works…",
      last_message_at: "2026-07-05T07:41:00Z",
      importance_score: 0.82,
      reasons: ["Unread", "Calendar"],
      labels: [{ name: "Unread", bg_color: "#e8f0fe", text_color: "#1a73e8" }],
      has_draft: true,
      draft_id: "d-9001",
    },
    {
      thread_id: "t-1003",
      subject: "Your invoice #INV-2043 is ready",
      from: "billing@vercel.com",
      snippet: "Your monthly invoice is now available. Amount due: $240.00…",
      last_message_at: "2026-07-04T22:03:00Z",
      importance_score: 0.44,
      reasons: ["Receipt"],
      labels: [{ name: "Receipts", bg_color: "#fef7e0", text_color: "#b06000" }],
      has_draft: false,
    },
  ],
};

const THREADS: Record<string, unknown> = {
  "t-1001": {
    thread_id: "t-1001",
    messages: [
      {
        message_id: "m-1",
        from: "Dana Whitfield <dana@northwind.vc>",
        to: "you@startup.com",
        date: "2026-07-05T09:14:00Z",
        subject: "Series A term sheet - final redlines",
        body_html:
          // Remote img mirrors services/_gmail_fake_backend.py: exercises the
          // blocked-by-default "Show images" banner (.invalid never resolves).
          "<p>Hi,</p><p>Great call today. Attaching the final redlines - one open point on the <b>liquidation preference</b> (we're proposing 1x non-participating).</p><p>If that works, we can sign this week.</p><p>Best,<br/>Dana</p><img src=\"https://img.invalid/northwind-logo.png\" alt=\"Northwind Ventures\">",
        attachments: [
          { filename: "termsheet-v7.pdf", mime_type: "application/pdf", size: 184320 },
        ],
      },
    ],
  },
  "t-1002": {
    thread_id: "t-1002",
    messages: [
      {
        message_id: "m-2",
        from: "Priya Nair <priya@peoplehq.io>",
        to: "you@startup.com",
        date: "2026-07-05T07:41:00Z",
        subject: "Re: Onsite interview loop for Staff Eng",
        body_text:
          "Confirming Thursday 10am-2pm. I've looped in the panel. Let me know if the schedule works and I'll send calendar holds.\n\nThanks,\nPriya",
        attachments: [],
      },
    ],
    draft: {
      draft_id: "d-9001",
      to: "priya@peoplehq.io",
      subject: "Re: Onsite interview loop for Staff Eng",
      body: "Thursday works great - please send the holds. Looking forward to it.",
      thread_id: "t-1002",
      attachments: [],
    },
  },
  "t-1003": {
    thread_id: "t-1003",
    messages: [
      {
        message_id: "m-3",
        from: "billing@vercel.com",
        to: "you@startup.com",
        date: "2026-07-04T22:03:00Z",
        subject: "Your invoice #INV-2043 is ready",
        body_text:
          "Your monthly invoice is now available. Amount due: $240.00. No action needed - auto-pay is on.",
        attachments: [],
      },
    ],
  },
};

// --- gmail_composer -------------------------------------------------------

const COMPOSER_DRAFT = {
  draft_id: "d-9001",
  to: "priya@peoplehq.io",
  cc: "",
  bcc: "",
  subject: "Re: Onsite interview loop for Staff Eng",
  body:
    "Hi Priya,\n\nThursday 10am–2pm works great - please send the calendar holds and I'll confirm with the panel on my side.\n\nLooking forward to it.\n\nBest,\nAlex",
  thread_id: "t-1002",
  attachments: [],
};

// --- dispatch -------------------------------------------------------------

/** The first tool result pushed to the app on init (drives the initial paint). */
// --- pdf_signer -----------------------------------------------------------

// A real (tiny) flat NDA, filled via the actual pdf_edit engine, so pdf.js
// renders authentic bytes. Regenerate with tests/pdf_fixtures.make_flat_pdf +
// services.pdf_edit_engine.apply_ops if the fixture builders change.
const NDA_FILLED_B64 =
  "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovVHlwZSAvQ2F0YWxvZwovUGFnZXMgMiAwIFIKPj4KZW5kb2JqCjIgMCBvYmoKPDwKL1R5cGUgL1BhZ2VzCi9Db3VudCAxCi9LaWRzIFsgMyAwIFIgXQo+PgplbmRvYmoKMyAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Ci9Gb250IDw8Ci9GMSA0IDAgUgovUGRmRWRpdEYxIDcgMCBSCj4+Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgNjEyIDc5MiBdCi9QYXJlbnQgMiAwIFIKL0NvbnRlbnRzIDUgMCBSCj4+CmVuZG9iago0IDAgb2JqCjw8Ci9UeXBlIC9Gb250Ci9TdWJ0eXBlIC9UeXBlMQovQmFzZUZvbnQgL0hlbHZldGljYQo+PgplbmRvYmoKNSAwIG9iagpbIDggMCBSIDkgMCBSIF0KZW5kb2JqCjYgMCBvYmoKPDwKL1Byb2R1Y2VyIChweXBkZikKPj4KZW5kb2JqCjcgMCBvYmoKPDwKL1R5cGUgL0ZvbnQKL1N1YnR5cGUgL1R5cGUxCi9CYXNlRm9udCAvSGVsdmV0aWNhCj4+CmVuZG9iago4IDAgb2JqCjw8Ci9MZW5ndGggMTc3Cj4+CnN0cmVhbQpxCkJUIC9GMSAxNCBUZiA3MiA3MDAgVGQgKE5PTi1ESVNDTE9TVVJFIEFHUkVFTUVOVCkgVGogRVQKQlQgL0YxIDEwIFRmIDcyIDY1MCBUZCAoTmFtZTopIFRqIEVUCkJUIC9GMSAxMCBUZiA3MiA2MDAgVGQgKERhdGU6KSBUaiBFVApCVCAvRjEgMTAgVGYgNzIgNTUwIFRkIChTaWduYXR1cmU6KSBUaiBFVAoKUQoKZW5kc3RyZWFtCmVuZG9iago5IDAgb2JqCjw8Ci9MZW5ndGggMTM2Cj4+CnN0cmVhbQpxCjAuMCAwLjAgNjEyIDc5MiByZQpXCm4KQlQKL1BkZkVkaXRGMSAxMCBUZgoxMTAgNjUwIFRkCihFaXRvIE1peWFtdXJhKSBUagpFVApCVAovUGRmRWRpdEYxIDEwIFRmCjEwNSA2MDAgVGQKKDIwMjZcMDU1MDdcMDU1MjIpIFRqCkVUClEKCmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDEwCjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDY0IDAwMDAwIG4gCjAwMDAwMDAxMjMgMDAwMDAgbiAKMDAwMDAwMDI3MiAwMDAwMCBuIAowMDAwMDAwMzQyIDAwMDAwIG4gCjAwMDAwMDAzNzMgMDAwMDAgbiAKMDAwMDAwMDQxMiAwMDAwMCBuIAowMDAwMDAwNDgyIDAwMDAwIG4gCjAwMDAwMDA3MTAgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSAxMAovUm9vdCAxIDAgUgovSW5mbyA2IDAgUgo+PgpzdGFydHhyZWYKODk3CiUlRU9GCg==";

const PDF_SIGNER_REQUEST = {
  doc_id: "doc-fixture-nda",
  status: "awaiting_user_signature",
  guidance: "The document is now locked for signing.",
};

const PDF_SIGNER_DOC = {
  doc_id: "doc-fixture-nda",
  filename: "nda.pdf",
  status: "awaiting_signature",
  page_count: 1,
  stamp_page: 1,
  // From services.pdf_signing.resolve_stamp_rect for placement (1, 140, 545).
  stamp_rect: [140.0, 541.0, 340.0, 577.0],
  data_base64: NDA_FILLED_B64,
};

// --- settings -------------------------------------------------------------

// Shape mirrors models.settings.SettingsSnapshot / SubView. A connected,
// actively-watching account with one webhook endpoint - the state that
// exercises every control on the panel (rotate, remove, add).
const SETTINGS_SNAPSHOT = {
  gmail_connected: true,
  gmail_email: "alex@startup.com",
  watching: true,
  watch_expiration: "2026-07-12T09:00:00Z",
  push_available: true,
  subscriptions: [
    {
      id: "sub-7f21",
      url: "https://hooks.startup.com/daysurface/inbox",
      event_types: ["message.received", "draft.saved"],
      active: true,
      created_at: "2026-06-28T16:20:00Z",
    },
  ],
};

export function initialResult(app: string): ToolResult {
  if (app === "gmail_composer") return ok(COMPOSER_DRAFT);
  if (app === "pdf_signer") return ok(PDF_SIGNER_REQUEST);
  if (app === "settings") return ok(SETTINGS_SNAPSHOT);
  return ok(INBOX_CURATE);
}

/** Answer an app's callServerTool by name, as a real server would. */
export function dispatch(name: string, args: Record<string, unknown>): ToolResult {
  switch (name) {
    case "gmail_inbox.refresh":
      return ok(INBOX_CURATE);
    case "gmail_inbox.open_thread":
    case "gmail_composer.get_thread":
      return ok(THREADS[String(args.thread_id)] ?? {});
    case "gmail_composer.refresh":
      return ok(COMPOSER_DRAFT);
    case "pdf_signer.get_document":
      return ok(PDF_SIGNER_DOC);
    case "pdf_signer.sign":
      return ok({
        doc_id: "doc-fixture-nda",
        status: "signed",
        signed_by: String(args.typed_name ?? "Signer"),
        signed_at_utc: "2026-07-22T12:00:00+00:00",
        message: `Signed by ${args.typed_name} on 2026-07-22T12:00:00+00:00.`,
      });
    case "pdf_signer.cancel":
      return ok({ doc_id: "doc-fixture-nda", status: "open" });
    case "settings.get":
      return ok(SETTINGS_SNAPSHOT);
    case "settings.subscribe":
      // Full models.webhooks.WebhookSubscribeResult - echo back the submitted
      // url/event_types the way the real tool does, not just the new secret.
      return ok({
        id: "sub-new01",
        url: String(args.url ?? ""),
        event_types: (args.event_types as string[] | null) ?? null,
        active: true,
        secret: "whsec_fixture_9c3ae10b4f27",
      });
    case "settings.rotate_secret":
      return ok({
        id: String(args.subscription_id ?? "sub-7f21"),
        secret: "whsec_fixture_rotated_4d81f2",
      });
    case "settings.unsubscribe":
      return ok({ unsubscribed: true });
    case "gmail_composer.send":
      // A real message_id so the composer's Sent state (and the model-context
      // push it triggers) carries a plausible identifier.
      return ok({ message_id: "msg-fixture-0042", thread_id: "t-1002" });
    default:
      // set_focus, mark_read, archive, mark_done, reply, forward, save_draft,
      // send, discard … acknowledged with an empty structured result.
      return ok({});
  }
}
