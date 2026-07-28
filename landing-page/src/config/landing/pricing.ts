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
 * interactive is free, autonomous is paid, and a team is one price rather than
 * a per-seat ladder. If a feature does not sit on one of the three axes in
 * `pricingAxes`, it is free.
 *
 * Three tiers, deliberately. An individual "Pro" sitting between Free and Team
 * only existed to make people who work alone pay for autonomy, and a per-seat
 * Business tier on top of it made SSO cost 5x more than the product. Both
 * collapsed into Team.
 */
import { site } from "./site";

export interface PricingTier {
  name: string;
  price: string;
  cadence?: string;
  description: string;
  features: string[];
  /**
   * Repo badge shown beside the tier name: the GitHub mark plus the repo name,
   * linking to the source. Only the free tier carries it, to mark at a glance
   * that this is the open-source, self-hostable tier.
   */
  repoBadge?: boolean;
  cta: string;
  href: string;
  /**
   * Small print under the price (member counts, billing notes).
   *
   * Note there is deliberately no "featured"/"most popular" flag. We are not
   * inventing social proof, and we are not steering people to a tier with
   * styling either - every card gets identical treatment and the table below
   * does the arguing.
   */
  note?: string;
}

/** Repo name parsed from the GitHub URL (e.g. "DaySurface"). */
export const repoName: string =
  site.githubUrl.replace(/\/+$/, "").split("/").pop() || site.name;

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
    "The full Gmail experience is free forever, and always will be. One paid tier covers your whole team, single sign-on included. Or self-host the whole thing, every feature, for nothing.",
  principle: "Interactive is free. Autonomous is paid. Your whole team is one price.",
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
        "MIT-licensed - self-host every feature",
        "Community support",
      ],
      cta: "Start free",
      href: "/#how-it-works",
      repoBadge: true,
    },
    {
      name: "Team",
      price: "$29",
      cadence: "/mo",
      description:
        "Everything DaySurface does, for you and up to four colleagues. It keeps working after you close the chat, and single sign-on is in the box.",
      features: [
        "Everything in Free, for all 5 members",
        "Follow-up Manager - nothing slips",
        "Webhooks + real-time inbox watch",
        "Scheduled rules",
        "Unlimited curation memory",
        "Unlimited PDF signing",
        "SSO / SAML / SCIM - self-serve, no sales call",
        "Shared team rules + shared mailboxes",
        "Audit log + admin console",
        "Unlimited connected mailboxes",
        "Priority support",
      ],
      cta: "Start 14-day trial",
      href: "/#how-it-works",
      note: "5 members included. $6/mo per extra member. No card up front.",
    },
    {
      name: "Enterprise",
      price: "Custom",
      description:
        "Procurement, uptime commitments, and deployment on infrastructure you control.",
      features: [
        "Everything in Team",
        "Unlimited members",
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
 * Self-hosting: the genuinely free option, on its own terms rather than as
 * small print under the hosted ladder. Everything on this page ships in the
 * MIT repo, and entitlement enforcement is off by default in the source, so a
 * self-hoster is not on "the free tier" - they have the whole product.
 */
export const selfHost: {
  eyebrow: string;
  heading: string;
  body: string;
  points: string[];
  cta: { label: string; href: string };
  secondary: { label: string; href: string };
} = {
  eyebrow: "Open source",
  heading: "Or run the whole thing yourself, for nothing",
  body: `${site.name} is MIT-licensed. Every feature on this page - Follow-up Manager, unlimited memory, SSO, the lot - is in the repo, and the entitlement checks that draw the tiers above are disabled by default in the source. Self-hosting is not a limited edition of the product. It is the product.`,
  points: [
    "No licence fee, no member count, no feature flags removed",
    "Ships with a Dockerfile and Railway config",
    "Your mail never touches our servers",
    "Fork it, audit it, or take it in-house permanently",
  ],
  cta: { label: "Get the source", href: site.githubUrl },
  secondary: { label: "Read the deploy guide", href: site.docsUrl },
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
      tier: "Team",
    },
    {
      name: "Organisation",
      free: "One person, one mailbox, one machine.",
      paid: "More than one of you. SSO, shared rules, audit log, admin console - at the same flat price, not per seat.",
      tier: "Team",
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
  values: [MatrixValue, MatrixValue, MatrixValue];
}

export interface PricingMatrixGroup {
  group: string;
  rows: PricingMatrixRow[];
}

/** Column order matches `pricing.tiers`: Free, Team, Enterprise. */
export const pricingMatrix: PricingMatrixGroup[] = [
  {
    group: "The inbox (always free)",
    rows: [
      {
        capability: "Read, search, triage",
        detail: "Your full Gmail history, not a recent window",
        values: [true, true, true],
      },
      {
        capability: "Draft, reply, send",
        values: [true, true, true],
      },
      {
        capability: "Interactive MCP Apps",
        detail: "In-chat composer and ranked inbox",
        values: [true, true, true],
      },
      {
        capability: "Self-host, all features",
        detail: "MIT licence, no crippled build",
        values: [true, true, true],
      },
    ],
  },
  {
    group: "Memory",
    rows: [
      {
        capability: "Curation memory",
        detail: "Banked triage verdicts, so repeat reads stay cheap. This is our memory of your mail, never your mail itself.",
        values: ["30 days", "Unlimited", "Custom"],
      },
      {
        capability: "Retention policy controls",
        values: [false, true, true],
      },
    ],
  },
  {
    group: "Autonomy",
    rows: [
      {
        capability: "Follow-up Manager",
        detail: "Chases what is owed to you and what you owe",
        values: [false, true, true],
      },
      {
        capability: "Webhook subscriptions",
        values: ["1", "Unlimited", "Unlimited"],
      },
      {
        capability: "Real-time inbox watch",
        values: [false, true, true],
      },
      {
        capability: "Scheduled rules",
        values: [false, true, true],
      },
    ],
  },
  {
    group: "Documents",
    rows: [
      {
        capability: "PDF form filling",
        values: [true, true, true],
      },
      {
        capability: "Signature ceremonies",
        detail: "You always sign, never the model",
        values: ["3 / month", "Unlimited", "Unlimited"],
      },
    ],
  },
  {
    group: "Team and governance",
    rows: [
      {
        capability: "Members",
        detail: "One flat price, not a per-seat ladder",
        values: ["1", "5 included, $6/mo after", "Unlimited"],
      },
      {
        capability: "Connected mailboxes",
        values: ["1", "Unlimited", "Unlimited"],
      },
      {
        capability: "Shared team rules",
        values: [false, true, true],
      },
      {
        capability: "SSO / SAML / SCIM",
        detail: "Self-serve checkout, not a sales call",
        values: [false, true, true],
      },
      {
        capability: "Audit log + admin console",
        values: [false, true, true],
      },
    ],
  },
  {
    group: "Support and terms",
    rows: [
      {
        capability: "Support",
        values: ["Community", "Priority", "Dedicated"],
      },
      {
        capability: "DPA + security review",
        values: [false, "DPA", true],
      },
      {
        capability: "Uptime SLA",
        values: [false, false, true],
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
    { when: "Day 30", what: "Your oldest memory starts ageing out. Team keeps it, free for 14 days.", gated: true },
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
      q: "I work alone. Do I have to pay for five members?",
      a: "You pay $29/mo whoever you are, and you can use one of the five or all of them. We would rather charge one simple price than run an individual tier that exists purely to charge solo users for the same thing. If you only ever want the free tier, that is a fine place to stay indefinitely.",
    },
    {
      q: "It is open source. Why is anything paid at all?",
      a: `${site.name} is MIT-licensed, so you can self-host every feature on this page for nothing, forever. What the paid tier buys is us running it: servers that keep watching your inbox and chasing your follow-ups while you are asleep, storage for memory that never expires, and the team features a solo self-hoster has no use for.`,
    },
    {
      q: "What happens if I downgrade or my card fails?",
      a: "You keep read access to everything, always. Writes and background automations stop, and retained memory is frozen rather than deleted for 30 days so resubscribing restores it intact. Being locked out of your own inbox tooling over an expired card is not something we are willing to do.",
    },
    {
      q: "Is SSO going to cost me a sales call?",
      a: "No. SSO, SAML, and SCIM are in Team at $29/mo with self-serve checkout. Charging enterprise money to turn on a login method is a tax, not a product. Enterprise pricing is for uptime commitments, DPAs, and dedicated deployment.",
    },
    {
      q: "Do I need a credit card to start?",
      a: "No. The free tier needs no card and never expires. The 14-day Team trial is offered when you first reach something Team covers, so you can see what it does before deciding.",
    },
    {
      q: "If I self-host, which features do I get?",
      a: "All of them. Entitlement checks are disabled by default in the source and are switched on only in our hosted deployment. We are not shipping a deliberately hobbled open-source build.",
    },
  ],
};
