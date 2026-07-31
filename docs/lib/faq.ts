/**
 * Per-page FAQ content, keyed by docs URL.
 *
 * Deliberately one source for two consumers: the `<Faq />` MDX component
 * renders these as visible page content, and `lib/structured-data.ts` emits the
 * same strings as `FAQPage` JSON-LD. Google requires that structured-data
 * answers be present verbatim in the visible page, so defining the Q&A twice -
 * once in MDX prose, once in a schema block - is a drift bug waiting to happen.
 * Add an entry here and render it with `<Faq page="/docs/..." />`.
 *
 * Answers are plain text (no markup): they are injected into JSON-LD, where
 * embedded HTML is allowed but limited, and rendered as paragraphs on the page.
 */

export interface FaqEntry {
  question: string;
  answer: string;
}

export const DOCS_FAQ: Record<string, readonly FaqEntry[]> = {
  "/docs/gmail-webhooks": [
    {
      question: "Does Gmail have webhooks?",
      answer:
        "Not directly. The Gmail API has no field for an HTTPS callback URL. It offers push notifications delivered through Google Cloud Pub/Sub, which you then have to turn into an HTTP webhook yourself - including watch renewal, history reconciliation, deduplication, signing, and retries. DaySurface ships that layer so you register an endpoint and receive signed POSTs.",
    },
    {
      question: "How do I get a webhook when a new email arrives in Gmail?",
      answer:
        "Create a Pub/Sub topic, grant gmail-api-push@system.gserviceaccount.com the Pub/Sub Publisher role on it, add a push subscription pointing at your server with OIDC authentication, and call users.watch() for each mailbox. Your server then calls users.history.list on every notification to find out what actually arrived, and re-emits it to subscriber endpoints.",
    },
    {
      question: "Does the Gmail push notification include the email itself?",
      answer:
        "No. The Pub/Sub message decodes to just an emailAddress and a historyId. It tells you something changed, not what changed. You must call users.history.list with your last stored history ID to learn which messages were added.",
    },
    {
      question: "Why did my Gmail webhook stop working after a week?",
      answer:
        "A Gmail watch expires after about 7 days and stops silently - no error, no final notification. You must call users.watch() again before it lapses. This is the most common failure mode in hand-rolled Gmail webhook integrations. DaySurface renews watches automatically on its periodic runner.",
    },
    {
      question: "What happens when users.history.list returns 404?",
      answer:
        "The history ID you stored has aged out of Gmail's retention window, which happens on quiet mailboxes as a matter of course. Treating it as a transient error stops delivery permanently. The correct response is to fall back to users.messages.list, resync, and adopt a fresh history ID.",
    },
    {
      question: "How do I verify a DaySurface webhook signature?",
      answer:
        "Compute an HMAC-SHA256 over the X-Webhook-Timestamp value, a literal period, and the raw request body, using your subscription secret. Compare it in constant time against the hex digest in the X-Webhook-Signature header, which is prefixed sha256=. Verify against raw bytes rather than re-serialized JSON, and reject requests whose timestamp is too old to prevent replays.",
    },
    {
      question: "Are webhook deliveries retried if my endpoint is down?",
      answer:
        "Yes. Any non-2xx response schedules a retry with exponential backoff starting at 30 seconds, doubling per attempt and capped at one hour, until the configured maximum (6 by default). Because retries are real, handlers must be idempotent - key on X-Webhook-Event-Id, which is stable across attempts of the same event.",
    },
    {
      question: "Can I receive the full message body in the webhook payload?",
      answer:
        "No. The payload carries metadata and roughly the first 200 characters as a snippet, never the full body. Fetch the message with gmail_get_thread when you need more, so mail content is pulled deliberately by an authenticated caller instead of being pushed to every registered endpoint.",
    },
  ],
};

export function getFaq(page: string): readonly FaqEntry[] | undefined {
  return DOCS_FAQ[page];
}
