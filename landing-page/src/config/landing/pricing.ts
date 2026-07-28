/**
 * Pricing: tiers, add-ons, the three-axis rationale, the full comparison
 * matrix, the free-forever promise, and the pricing FAQ.
 *
 * Source of truth for BOTH the homepage `#pricing` teaser (Pricing.astro) and
 * the dedicated /pricing page, plus the machine-readable /pricing.md manifest
 * that answer engines read (see src/agent/content.ts).
 *
 * The rule every row below has to justify itself against: interactive is free,
 * autonomous is paid, and the tiers above Team sell throughput rather than
 * features. If a capability does not sit on one of the three axes in
 * `pricingAxes`, it is free.
 *
 * Shape: Free -> Team -> Scaling -> Enterprise, with governance sold as an
 * add-on that attaches to any paid tier. Splitting scale (a tier) from
 * governance (an add-on) is deliberate: a 40-person team that needs Okta
 * should not have to buy throughput it will not use, and a high-volume team of
 * six should not have to buy an enterprise contract to get rate limits.
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
    "The full Gmail experience is free forever, and always will be. Paid tiers buy autonomy and throughput, governance is an add-on you attach when you need it, and you can self-host the whole thing for nothing.",
  principle: "Interactive is free. Autonomous is paid. Scale when you need to.",
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
        "500 tool calls per day",
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
        "Everything DaySurface does, for you and up to four colleagues. It keeps working after you close the chat.",
      features: [
        "Everything in Free, for all 5 members",
        "Follow-up Manager - nothing slips",
        "Webhooks + real-time inbox watch",
        "Scheduled rules",
        "Unlimited curation memory",
        "Unlimited PDF signing",
        "Shared team rules + shared mailboxes",
        "Audit log + admin console",
        "5,000 tool calls per day",
        "Priority support",
      ],
      cta: "Start 14-day trial",
      href: "/#how-it-works",
      note: "5 members included. $6/mo per extra member. No card up front.",
    },
    {
      name: "Scaling",
      price: "$199",
      cadence: "/mo",
      description:
        "Same product, far more headroom. For teams running agents against their mail all day rather than a few times an hour.",
      features: [
        "Everything in Team",
        "25 members included",
        "50,000 tool calls per day",
        "25 concurrent agent sessions",
        "100k webhook deliveries per day",
        "Priority background job queue",
        "Higher curation throughput",
        "Usage analytics + per-member breakdown",
        "Priority support with a response target",
      ],
      cta: "Start 14-day trial",
      href: "/#how-it-works",
      note: "25 members included. $5/mo per extra member.",
    },
    {
      name: "Enterprise",
      price: "Custom",
      description:
        "Procurement, uptime commitments, and deployment on infrastructure you control.",
      features: [
        "Everything in Scaling",
        "Unlimited members and custom limits",
        "Governance add-on included",
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
 * Add-ons attach to a paid tier rather than forming a rung of their own, so
 * buying Okta support never means buying throughput you will not use. Each one
 * renders as an inset "+ optional" panel inside the card named by `attachTo`,
 * which keeps the ladder four cards wide however many add-ons we sell.
 */
export interface AddOn {
  name: string;
  price: string;
  cadence: string;
  /** Long form. Not rendered on the card - used by the /pricing.md manifest. */
  description: string;
  features: string[];
  /** Which tiers this can be attached to, shown as small print. */
  availableOn: string;
  /** Name of the tier whose card this panel renders inside. */
  attachTo: string;
}

export const addOns: { items: AddOn[] } = {
  items: [
    {
      name: "Governance add-on",
      price: "$300",
      cadence: "/mo",
      description:
        "What a security team asks for before it will sign off. Priced as an add-on so a six-person team can buy Okta support without buying an enterprise contract to go with it.",
      features: [
        "Enterprise SSO (e.g. Okta)",
        "SSO enforcement",
        "Fine-grained RBAC",
        "Support via dedicated Slack / MS Teams channel",
      ],
      availableOn: "Also available on Team. Included in Enterprise.",
      attachTo: "Scaling",
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
  body: `${site.name} is MIT-licensed. Every feature on this page - Follow-up Manager, unlimited memory, SSO, RBAC, the lot - is in the repo, and the entitlement checks that draw the tiers above are disabled by default in the source. Self-hosting is not a limited edition of the product. It is the product.`,
  points: [
    "No licence fee, no member count, no rate limits",
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
    "We do not paywall by vibes. A capability costs money only if it sits on one of these three axes. Everything else is free, permanently.",
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
      paid: "More than one of you. Shared rules and audit log in Team; enterprise SSO and RBAC as an add-on.",
      tier: "Team + add-on",
    },
    {
      name: "Unit cost",
      free: "Bounded per-call work, at a rate one human can generate.",
      paid: "Volume. Tool calls, concurrent sessions, webhook deliveries, retained memory.",
      tier: "Scaling",
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

/** Column order matches `pricing.tiers`: Free, Team, Scaling, Enterprise. */
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
    group: "Scale and limits",
    rows: [
      {
        capability: "Tool calls per day",
        values: ["500", "5,000", "50,000", "Custom"],
      },
      {
        capability: "Concurrent agent sessions",
        values: ["1", "5", "25", "Custom"],
      },
      {
        capability: "Webhook deliveries",
        values: ["100 / day", "10k / day", "100k / day", "Custom"],
      },
      {
        capability: "Background job queue",
        detail: "Where your watches and follow-up sweeps sit",
        values: [false, "Standard", "Priority", "Dedicated"],
      },
      {
        capability: "Members",
        detail: "Bundled, not a per-seat ladder",
        values: ["1", "5 incl, $6/mo after", "25 incl, $5/mo after", "Unlimited"],
      },
      {
        capability: "Connected mailboxes",
        values: ["1", "Unlimited", "Unlimited", "Unlimited"],
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
        values: [false, true, true, true],
      },
    ],
  },
  {
    group: "Autonomy",
    rows: [
      {
        capability: "Follow-up Manager",
        detail: "Chases what is owed to you and what you owe",
        values: [false, true, true, true],
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
        capability: "Sign in with Google",
        values: [true, true, true, true],
      },
      {
        capability: "Shared team rules + mailboxes",
        values: [false, true, true, true],
      },
      {
        capability: "Audit log + admin console",
        values: [false, true, true, true],
      },
      {
        capability: "Enterprise SSO (Okta, SAML)",
        detail: "Governance add-on, $300/mo",
        values: [false, "Add-on", "Add-on", true],
      },
      {
        capability: "SSO enforcement",
        detail: "Governance add-on, $300/mo",
        values: [false, "Add-on", "Add-on", true],
      },
      {
        capability: "Fine-grained RBAC",
        detail: "Governance add-on, $300/mo",
        values: [false, "Add-on", "Add-on", true],
      },
    ],
  },
  {
    group: "Support and terms",
    rows: [
      {
        capability: "Support",
        values: ["Community", "Priority", "Priority + target", "Dedicated"],
      },
      {
        capability: "Dedicated Slack / MS Teams channel",
        detail: "Governance add-on, $300/mo",
        values: [false, "Add-on", "Add-on", true],
      },
      {
        capability: "DPA + security review",
        values: [false, "DPA", "DPA", true],
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
      q: "What is the difference between Team and Scaling?",
      a: "Headroom, not features. Scaling is the same product with roughly ten times the tool calls, five times the concurrent sessions, a priority background queue, and 25 members instead of 5. If your agents work your inbox continuously rather than in bursts, you will feel Team's ceiling. If they do not, Team is the right tier and we would rather you stayed on it.",
    },
    {
      q: "Why is enterprise SSO an add-on rather than a tier?",
      a: "Because the two things are unrelated. A six-person team that needs Okta should not have to buy throughput for 25 people to get it, and a high-volume team of six should not need an enterprise contract to raise its rate limits. The Governance add-on is $300/mo, attaches to Team or Scaling, and is self-serve. Ordinary Google sign-in is free on every tier, including Free.",
    },
    {
      q: "I work alone. Do I have to pay for five members?",
      a: "You pay $29/mo whoever you are, and you can use one of the five or all of them. We would rather charge one simple price than run an individual tier that exists purely to charge solo users for the same thing. If you only ever want the free tier, that is a fine place to stay indefinitely.",
    },
    {
      q: "It is open source. Why is anything paid at all?",
      a: `${site.name} is MIT-licensed, so you can self-host every feature on this page for nothing, forever, with no rate limits. What the paid tiers buy is us running it: servers that keep watching your inbox and chasing your follow-ups while you are asleep, storage for memory that never expires, throughput we have to provision, and the governance features a solo self-hoster has no use for.`,
    },
    {
      q: "What happens if I downgrade or my card fails?",
      a: "You keep read access to everything, always. Writes and background automations stop, and retained memory is frozen rather than deleted for 30 days so resubscribing restores it intact. Being locked out of your own inbox tooling over an expired card is not something we are willing to do.",
    },
    {
      q: "Do I need a credit card to start?",
      a: "No. The free tier needs no card and never expires. The 14-day trial is offered when you first reach something a paid tier covers, so you can see what it does before deciding.",
    },
    {
      q: "If I self-host, which features do I get?",
      a: "All of them, with no rate limits. Entitlement checks are disabled by default in the source and are switched on only in our hosted deployment. We are not shipping a deliberately hobbled open-source build.",
    },
  ],
};
