/**
 * Pricing: tiers, the three-axis rationale, the full comparison matrix, the
 * free-forever promise, and the pricing FAQ.
 *
 * Source of truth for BOTH the homepage `#pricing` teaser (Pricing.astro) and
 * the dedicated /pricing page, plus the machine-readable /pricing.md manifest
 * that answer engines read (see src/agent/content.ts).
 *
 * The tiering rationale lives in manual_docs/pricing_strategy.md. The short
 * version, and the thing every row below has to justify itself against:
 * interactive is free, autonomous is paid, organisational is Business. If a
 * feature does not sit on one of the three axes in `pricingAxes`, it is free.
 */
import { site } from "./site";

export interface PricingTier {
  name: string;
  price: string;
  cadence?: string;
  description: string;
  features: string[];
  cta: string;
  href: string;
  featured?: boolean;
  /** Small print under the price (seat minimums, billing notes). */
  note?: string;
}

export const pricing: {
  enabled: boolean;
  heading: string;
  subhead: string;
  /** The one-line rule the whole table follows. */
  principle: string;
  tiers: PricingTier[];
} = {
  // Surfaced on the homepage AND in the machine-readable /pricing.md manifest.
  // Flip to false to hide the on-page section (the manifest still generates).
  enabled: true,
  heading: "Pricing",
  subhead:
    "The full Gmail experience is free forever, and always will be. You pay when DaySurface starts working while you are not watching, or when your whole team needs it.",
  principle: "Interactive is free. Autonomous is paid. Organisational is Business.",
  tiers: [
    {
      name: "Free",
      price: "$0",
      cadence: "forever",
      description:
        "Everything you can do in the Gmail web UI, done through your agent instead. Not a trial.",
      features: [
        "Read, search, and triage your whole inbox",
        "Draft, reply, and send",
        "Interactive MCP Apps (composer + ranked inbox)",
        "1 connected mailbox",
        "30 days of curation memory",
        "3 PDF signatures per month",
        "MIT-licensed - self-host the lot",
        "Community support",
      ],
      cta: "Start free",
      href: "/#how-it-works",
    },
    {
      name: "Pro",
      price: "$20",
      cadence: "/mo",
      description:
        "It keeps working after you close the chat. Follow-ups get chased, mail gets watched, memory never expires.",
      features: [
        "Everything in Free",
        "Follow-up Manager - nothing slips",
        "Webhooks + real-time inbox watch",
        "Unlimited curation memory",
        "Unlimited PDF signing",
        "5 connected mailboxes",
        "Email support",
      ],
      cta: "Start 14-day trial",
      href: "/#how-it-works",
      featured: true,
      note: "No card up front. Free tier never expires.",
    },
    {
      name: "Business",
      price: "$30",
      cadence: "/seat/mo",
      description:
        "For more than one of you. Single sign-on, shared triage rules, and an audit trail.",
      features: [
        "Everything in Pro",
        "SSO / SAML / SCIM - self-serve, no sales call",
        "Shared team rules + shared mailboxes",
        "Audit log + admin console",
        "Retention policy controls",
        "Unlimited connected mailboxes",
        "Priority support",
      ],
      cta: "Start 14-day trial",
      href: "/#how-it-works",
      note: "5 seat minimum. Billed per active seat.",
    },
    {
      name: "Enterprise",
      price: "Custom",
      description:
        "Procurement, uptime commitments, and deployment on infrastructure you control.",
      features: [
        "Everything in Business",
        "Uptime SLA + DPA",
        "Dedicated or VPC deployment",
        "Security review + procurement support",
        "Self-host support contract",
        "Named onboarding + dedicated support",
      ],
      cta: "Contact sales",
      href: "/#how-it-works",
    },
  ],
};

/**
 * Why a given feature is paid. Three axes, not a feature-by-feature argument -
 * a tier customers can predict is a tier they stop litigating.
 */
export const pricingAxes: {
  heading: string;
  subhead: string;
  items: { name: string; free: string; paid: string; tier: string }[];
} = {
  heading: "What we charge for, and why",
  subhead:
    "We do not paywall by vibes. A feature costs money only if it sits on one of these three axes. Everything else is free, permanently.",
  items: [
    {
      name: "Autonomy",
      free: "You are in the chat. Reading, searching, triaging, drafting, sending.",
      paid: "It runs while you sleep. Watches, webhooks, background follow-up, scheduled rules.",
      tier: "Pro",
    },
    {
      name: "Organisation",
      free: "One person, one mailbox, one machine.",
      paid: "More than one of you. SSO, seats, shared rules, audit log, admin console.",
      tier: "Business",
    },
    {
      name: "Unit cost",
      free: "Bounded per-call work. Gmail passthrough costs us almost nothing.",
      paid: "Retained memory, stored documents, delivered webhooks. Free allowance, then metered.",
      tier: "Metered",
    },
  ],
};

/**
 * The promise. Deliberately specific and deliberately load-bearing: naming the
 * tools makes it expensive for future-us to quietly walk it back.
 */
export const alwaysFree: { heading: string; body: string; items: string[] } = {
  heading: "Free forever, no asterisk",
  body: "Everything you can do in the Gmail web UI, you can do through your agent, for nothing. This list does not shrink. If we ever need to charge for something new, it will be something Gmail does not do either.",
  items: [
    "List and read any thread",
    "Search your entire mail history",
    "Triage and rank your inbox",
    "Compose, reply, and send",
    "Archive and mark done",
    "Manage drafts and attachments",
    "Connect and disconnect your account",
    "Interactive in-chat inbox + composer",
  ],
};

export type MatrixValue = boolean | string;

export interface PricingMatrixRow {
  capability: string;
  detail?: string;
  values: [MatrixValue, MatrixValue, MatrixValue, MatrixValue];
}

export interface PricingMatrixGroup {
  group: string;
  rows: PricingMatrixRow[];
}

/** Column order matches `pricing.tiers`: Free, Pro, Business, Enterprise. */
export const pricingMatrix: PricingMatrixGroup[] = [
  {
    group: "The inbox (always free)",
    rows: [
      {
        capability: "Read, search, triage",
        detail: "Your full Gmail history, not a recent window",
        values: [true, true, true, true],
      },
      {
        capability: "Draft, reply, send",
        values: [true, true, true, true],
      },
      {
        capability: "Interactive MCP Apps",
        detail: "In-chat composer and ranked inbox",
        values: [true, true, true, true],
      },
      {
        capability: "Self-host, all features",
        detail: "MIT licence, no crippled build",
        values: [true, true, true, true],
      },
    ],
  },
  {
    group: "Memory",
    rows: [
      {
        capability: "Curation memory",
        detail: "Banked triage verdicts, so repeat reads stay cheap. This is our memory of your mail, never your mail itself.",
        values: ["30 days", "Unlimited", "Unlimited", "Custom"],
      },
      {
        capability: "Retention policy controls",
        values: [false, false, true, true],
      },
    ],
  },
  {
    group: "Autonomy",
    rows: [
      {
        capability: "Follow-up Manager",
        detail: "Chases what is owed to you and what you owe",
        values: [false, true, "Shared", "Shared"],
      },
      {
        capability: "Webhook subscriptions",
        values: ["1", "Unlimited", "Unlimited", "Unlimited"],
      },
      {
        capability: "Real-time inbox watch",
        values: [false, true, true, true],
      },
      {
        capability: "Scheduled rules",
        values: [false, true, true, true],
      },
    ],
  },
  {
    group: "Documents",
    rows: [
      {
        capability: "PDF form filling",
        values: [true, true, true, true],
      },
      {
        capability: "Signature ceremonies",
        detail: "You always sign, never the model",
        values: ["3 / month", "Unlimited", "Unlimited", "Unlimited"],
      },
    ],
  },
  {
    group: "Team and governance",
    rows: [
      {
        capability: "Connected mailboxes",
        values: ["1", "5", "Unlimited", "Unlimited"],
      },
      {
        capability: "Shared team rules",
        values: [false, false, true, true],
      },
      {
        capability: "SSO / SAML / SCIM",
        detail: "Self-serve checkout, not a sales call",
        values: [false, false, true, true],
      },
      {
        capability: "Audit log + admin console",
        values: [false, false, true, true],
      },
    ],
  },
  {
    group: "Support and terms",
    rows: [
      {
        capability: "Support",
        values: ["Community", "Email", "Priority", "Dedicated"],
      },
      {
        capability: "DPA + security review",
        values: [false, false, "DPA", true],
      },
      {
        capability: "Uptime SLA",
        values: [false, false, false, true],
      },
    ],
  },
];

/**
 * The paywall journey. We only ask for money after the product has already
 * worked for you, so every step here is a thing that happened before a wall.
 */
export const pricingJourney: {
  heading: string;
  subhead: string;
  steps: { when: string; what: string; gated: boolean }[];
} = {
  heading: "You will have used it for a month before we ask",
  subhead:
    "There is no card up front and no locked front door. The free tier is the trial, and it does not expire.",
  steps: [
    { when: "Minute 1", what: "Connect Gmail, triage the inbox, send a reply.", gated: false },
    { when: "Week 1", what: "Curation memory builds up. Triage gets sharper and cheaper.", gated: false },
    { when: "Week 2", what: "Sign a PDF straight out of an attachment. Twice more, free.", gated: false },
    { when: "Day 30", what: "Your oldest memory starts ageing out. Pro keeps it, free for 14 days.", gated: true },
  ],
};

export const pricingFaq: { heading: string; items: { q: string; a: string }[] } = {
  heading: "Pricing questions",
  items: [
    {
      q: "Does the 30-day limit mean I cannot read old email?",
      a: "No, and this is the important one. Your mail lives in Gmail and search goes straight to Google's index, so you can find a thread from six years ago on the free tier. The 30 days applies only to DaySurface's own memory: the triage verdicts, summaries, and document sessions we generate and store for you. We would never hold your mail hostage, not least because we do not hold it.",
    },
    {
      q: "It is open source. Why is anything paid at all?",
      a: `${site.name} is MIT-licensed, so you can self-host every feature on this page for nothing, forever. What the paid tiers buy is us running it: servers that keep watching your inbox and chasing your follow-ups while you are asleep, storage for memory that never expires, and the organisational features a solo self-hoster has no use for.`,
    },
    {
      q: "What happens if I downgrade or my card fails?",
      a: "You keep read access to everything, always. Writes and background automations stop, and retained memory is frozen rather than deleted for 30 days so resubscribing restores it intact. Being locked out of your own inbox tooling over an expired card is not something we are willing to do.",
    },
    {
      q: "Is SSO going to cost me a sales call?",
      a: "No. SSO, SAML, and SCIM are on Business with self-serve checkout. Enterprise pricing is for uptime commitments, DPAs, and dedicated deployment, not for turning on a login method.",
    },
    {
      q: "Do I need a credit card to start?",
      a: "No. The free tier needs no card and never expires. The 14-day Pro trial is offered when you first reach something Pro covers, so you can see what it does before deciding.",
    },
    {
      q: "If I self-host, which features do I get?",
      a: "All of them. Entitlement checks are disabled by default in the source and are switched on only in our hosted deployment. We are not shipping a deliberately hobbled open-source build.",
    },
  ],
};
