/**
 * Copy for the /gmail-webhooks page.
 *
 * Search intent this page answers is commercial - "how do I get webhooks for a
 * Gmail inbox" - and it is deliberately kept apart from
 * `/docs/gmail-webhooks`, which answers the implementation question (Pub/Sub
 * topics, OIDC audiences, HMAC verification). Two pages, two intents, distinct
 * titles, cross-linked: the pair should not compete for the same query.
 *
 * Accuracy rules, same as `comparison.ts` - the claims here have to survive a
 * reader who then goes and reads the docs:
 * - The hosted service owns the Gmail watch and the Pub/Sub hop, so "no
 *   pipeline to run" is true for hosted users and NOT true for self-hosters,
 *   who set `GMAIL_PUBSUB_TOPIC` themselves. `selfHostNote` carries that
 *   caveat; do not delete it to tighten the pitch.
 * - Payload claims must match the envelope in the docs page: metadata and a
 *   snippet, never the full message body.
 */

export interface WebhookStage {
  /** Short label shown in the flow diagram. */
  label: string;
  /** Who is responsible for this hop. */
  owner: "gmail" | "daysurface" | "you";
  /** One-line detail, shown under the label. */
  detail: string;
}

export interface WebhookBenefit {
  title: string;
  body: string;
}

export interface WebhookFaq {
  /** Phrased as a question a person would actually type or ask. */
  question: string;
  answer: string;
}

export const webhooks: {
  heading: string;
  subhead: string;
  /** Left-to-right delivery path, rendered as the page's diagram. */
  stages: WebhookStage[];
  benefits: WebhookBenefit[];
  faqs: WebhookFaq[];
  selfHostNote: string;
  docsHref: string;
} = {
  heading: "Gmail webhooks without running a Pub/Sub pipeline",
  subhead:
    "Register an HTTPS endpoint and get a signed POST when new mail lands. The Gmail watch, the Pub/Sub push hop, deduplication, and delivery retries are ours to keep running.",

  stages: [
    {
      label: "New mail",
      owner: "gmail",
      detail: "Gmail notifies the watch on a connected inbox.",
    },
    {
      label: "DaySurface",
      owner: "daysurface",
      detail: "Verifies the push, de-duplicates, renews the watch before it expires.",
    },
    {
      label: "Your endpoint",
      owner: "you",
      detail: "Signed POST, retried with backoff until it gets a 2xx.",
    },
  ],

  benefits: [
    {
      title: "Signed and replay-proof",
      body: "Every POST carries an HMAC signature over a timestamped body, so you can reject forgeries and stale replays with your stored secret.",
    },
    {
      title: "Durable, not fire-and-forget",
      body: "Deliveries queue in an outbox and retry with backoff. A subscriber that is down for an hour does not lose the events it missed.",
    },
    {
      title: "Endpoints your users manage",
      body: "Adding an endpoint, copying its one-time secret, and rotating it are self-service - no operator ticket and no redeploy.",
    },
  ],

  faqs: [
    {
      question: "Do I need to set up Google Pub/Sub?",
      answer:
        "Not on the hosted service - the Gmail watch and the Pub/Sub push subscription belong to us. You register an HTTPS endpoint and start receiving events.",
    },
    {
      question: "What is in the webhook payload?",
      answer:
        "An event envelope with the message and thread ids, labels, sender, subject, date, and roughly the first 200 characters as a snippet. Never the full message body.",
    },
    {
      question: "How do I verify a delivery came from DaySurface?",
      answer:
        "Recompute the HMAC over the raw body prefixed with the request timestamp, compare it against the signature header in constant time, and reject timestamps that are too old.",
    },
    {
      question: "What happens if my endpoint is down?",
      answer:
        "The delivery is retried with backoff for several attempts before it is marked failed. Gmail processing is decoupled from delivery, so a failing subscriber never blocks the inbox.",
    },
    {
      question: "Can I subscribe to only some events?",
      answer:
        "Yes - a subscription can filter by event type, or omit the filter to receive everything.",
    },
  ],

  selfHostNote:
    "Self-hosting instead? You supply the Pub/Sub topic and the push subscription; the setup is four steps in the Google Cloud console.",

  docsHref: "/docs/gmail-webhooks",
};
