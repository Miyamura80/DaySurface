/**
 * Content for `/gmail-webhooks`, the standalone Gmail-webhooks resource.
 *
 * This page exists because "Gmail webhook" and its neighbours are a real query
 * cluster that the docs answered badly: `/docs/gmail-webhooks` is operator
 * reference written for someone who already runs the server, so it never
 * addressed the question the searches are actually asking - Gmail has no
 * webhook field, so now what?
 *
 * The two pages are deliberately split by intent and must stay that way. This
 * one owns the explanation and the pitch; the doc owns configuration keys, GCP
 * steps and runner modes. Moving the "Gmail has no native webhooks" framing
 * back into the doc would put two same-origin URLs on one query and let Google
 * pick between them.
 */
import type { FaqItem } from "./content";

/** One thing Gmail's push notifications leave you to build yourself. */
export interface WebhookGap {
  title: string;
  body: string;
}

/** A row of the polling / raw-Pub-Sub / DaySurface comparison. */
export interface WebhookApproach {
  capability: string;
  polling: string;
  pubsub: string;
  us: string;
}

export const webhooks: {
  heading: string;
  subhead: string;
  lede: string;
  gapsHeading: string;
  gaps: WebhookGap[];
  approachHeading: string;
  approaches: WebhookApproach[];
  faqHeading: string;
  faq: FaqItem[];
} = {
  heading: "Gmail webhooks",
  subhead: "Gmail does not have them. Here is what it has instead.",
  lede:
    "There is no field in the Gmail API where you paste an HTTPS URL and start " +
    "receiving POSTs when mail arrives. What Google offers is push notification " +
    "over Cloud Pub/Sub, and the distance between that and a webhook you would " +
    "want to depend on is most of the work. DaySurface closes it: register an " +
    "endpoint, get signed and retried deliveries on new mail.",

  gapsHeading: "Five things Pub/Sub leaves you to build",
  gaps: [
    {
      title: "The notification carries no mail",
      body:
        "The message body decodes to an email address and a history ID. No sender, " +
        "no subject, no snippet, no message ID. Every notification costs you a " +
        "users.history.list round trip before it means anything at all.",
    },
    {
      title: "History IDs expire",
      body:
        "users.history.list returns 404 once your stored history ID ages out of " +
        "Gmail's retention window, which happens on quiet mailboxes as a matter of " +
        "course. Treat that as a transient error and delivery stops permanently; the " +
        "correct response is a full resync onto a fresh ID.",
    },
    {
      title: "The watch dies every 7 days, silently",
      body:
        "users.watch expires after roughly a week with no warning, no final " +
        "notification and no error. Notifications simply stop. This is the single " +
        "most common reason a hand-rolled Gmail webhook works in testing and dies in " +
        "production a fortnight later.",
    },
    {
      title: "Pub/Sub delivers at least once",
      body:
        "The same message ID can and will arrive more than once. Without a dedup " +
        "table keyed on it, every duplicate notification becomes a duplicate " +
        "downstream webhook and your subscribers see the same mail twice.",
    },
    {
      title: "One subscription, one endpoint, no signatures",
      body:
        "A push subscription posts to a single URL you configure in the Cloud " +
        "console. If your users are to register their own endpoints, the fan-out, " +
        "the signing, the retry schedule and the delivery bookkeeping are all yours " +
        "to write. Pub/Sub supplies none of it.",
    },
  ],

  approachHeading: "Three ways to do it",
  approaches: [
    {
      capability: "Latency",
      polling: "Your poll interval",
      pubsub: "Seconds",
      us: "Seconds",
    },
    {
      capability: "Google Cloud setup",
      polling: "None",
      pubsub: "Topic, subscription, IAM",
      us: "Topic, subscription, IAM",
    },
    {
      capability: "Watch renewal",
      polling: "Not applicable",
      pubsub: "You build it",
      us: "Automatic",
    },
    {
      capability: "History 404 resync",
      polling: "You build it",
      pubsub: "You build it",
      us: "Automatic",
    },
    {
      capability: "Duplicate suppression",
      polling: "You build it",
      pubsub: "You build it",
      us: "On Pub/Sub message ID",
    },
    {
      capability: "Signed payloads",
      polling: "You build it",
      pubsub: "You build it",
      us: "HMAC-SHA256",
    },
    {
      capability: "Retries on subscriber failure",
      polling: "You build it",
      pubsub: "You build it",
      us: "Bounded exponential backoff",
    },
    {
      capability: "Per-user endpoints",
      polling: "You build it",
      pubsub: "Not supported",
      us: "Self-service",
    },
  ],

  faqHeading: "Gmail webhook questions",
  // Mirrored by `docs/lib/faq.ts` on the documentation side. Both render their
  // answers visibly and feed the same text to FAQPage JSON-LD, which requires
  // the answer to appear in the page body.
  faq: [
    {
      q: "Does Gmail have webhooks?",
      a: "Not directly. The Gmail API has no field for an HTTPS callback URL. It offers push notifications delivered through Google Cloud Pub/Sub, which you then have to turn into an HTTP webhook yourself - including watch renewal, history reconciliation, deduplication, signing and retries. DaySurface ships that layer, so you register an endpoint and receive signed POSTs.",
    },
    {
      q: "How do I get a webhook when a new email arrives in Gmail?",
      a: "Create a Pub/Sub topic, grant gmail-api-push@system.gserviceaccount.com the Pub/Sub Publisher role on it, add a push subscription pointing at your server with OIDC authentication, and call users.watch for each mailbox. Your server then calls users.history.list on every notification to find out what actually arrived, and re-emits it to subscriber endpoints.",
    },
    {
      q: "Does the Gmail push notification include the email itself?",
      a: "No. The Pub/Sub message decodes to just an email address and a history ID. It tells you something changed, not what changed. You must call users.history.list with your last stored history ID to learn which messages were added.",
    },
    {
      q: "Why did my Gmail webhook stop working after a week?",
      a: "A Gmail watch expires after about 7 days and stops silently - no error, no final notification. You must call users.watch again before it lapses. This is the most common failure mode in hand-rolled Gmail webhook integrations. DaySurface renews watches automatically on its periodic runner.",
    },
    {
      q: "Is polling Gmail a reasonable alternative?",
      a: "Yes, if you can tolerate the latency. Polling users.messages.list on a timer needs no Google Cloud project, no topic and no IAM, and it cannot silently expire the way a watch can. You trade freshness and API quota for a much smaller operational surface. Push is worth it when seconds matter or when mailbox count makes polling expensive.",
    },
    {
      q: "How do I verify a DaySurface webhook signature?",
      a: "Compute an HMAC-SHA256 over the X-Webhook-Timestamp value, a literal period, and the raw request body, using your subscription secret. Compare it in constant time against the hex digest in the X-Webhook-Signature header, which is prefixed sha256=. Verify against raw bytes rather than re-serialized JSON, and reject requests whose timestamp is too old to prevent replays.",
    },
    {
      q: "Are webhook deliveries retried if my endpoint is down?",
      a: "Yes. Any non-2xx response schedules a retry with exponential backoff starting at 30 seconds, doubling per attempt and capped at one hour, until the configured maximum (6 by default). Because retries are real, handlers must be idempotent - key on X-Webhook-Event-Id, which is stable across attempts of the same event.",
    },
    {
      q: "Can I receive the full message body in the webhook payload?",
      a: "No. The payload carries metadata and roughly the first 200 characters as a snippet, never the full body. Fetch the message with the gmail_get_thread tool when you need more, so mail content is pulled deliberately by an authenticated caller instead of being pushed to every registered endpoint.",
    },
  ],
};
