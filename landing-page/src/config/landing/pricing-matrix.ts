/**
 * The tier-by-tier feature matrix rendered by PricingTable.astro.
 *
 * Split out of ./pricing.ts, which sits near the repo's 500-line source cap
 * (scripts/check_file_length.py). The seam is deliberate: this module is the
 * evidence, ./pricing.ts is the offer.
 */
import type { TierId } from "./pricing";

export type MatrixValue = boolean | string;

export interface PricingMatrixRow {
  capability: string;
  detail?: string;
  /**
   * Keyed by TierId, not positional. A tuple made column order load-bearing:
   * reordering `pricing.tiers` would have re-pointed every row at the wrong
   * column with no type error. Adding a tier now fails to compile here, which
   * is the correct outcome.
   */
  values: Record<TierId, MatrixValue>;
}

export interface PricingMatrixGroup {
  group: string;
  rows: PricingMatrixRow[];
}

export const pricingMatrix: PricingMatrixGroup[] = [
  {
    group: "The inbox (always free)",
    rows: [
      {
        capability: "Read, search, triage",
        detail: "Your full Gmail history, not a recent window",
        values: { free: true, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Draft, reply, send",
        values: { free: true, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Interactive MCP Apps",
        detail: "In-chat composer and ranked inbox",
        values: { free: true, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Self-host, all features",
        detail: "MIT licence, no crippled build",
        values: { free: true, team: true, scaling: true, enterprise: true },
      },
    ],
  },
  {
    group: "Scale and limits",
    rows: [
      {
        capability: "Tool calls per day",
        values: { free: "500", team: "5,000", scaling: "50,000", enterprise: "Custom" },
      },
      {
        capability: "Concurrent agent sessions",
        values: { free: "1", team: "5", scaling: "25", enterprise: "Custom" },
      },
      {
        capability: "Webhook deliveries",
        values: { free: "100 / day", team: "10k / day", scaling: "100k / day", enterprise: "Custom" },
      },
      {
        capability: "Background job queue",
        detail: "Where your watches and follow-up sweeps sit",
        values: { free: false, team: "Standard", scaling: "Priority", enterprise: "Dedicated" },
      },
      {
        capability: "Members",
        detail: "Bundled, not a per-seat ladder",
        values: { free: "1", team: "5 incl, $6/mo after", scaling: "25 incl, $5/mo after", enterprise: "Unlimited" },
      },
      {
        capability: "Connected mailboxes",
        values: { free: "1", team: "Unlimited", scaling: "Unlimited", enterprise: "Unlimited" },
      },
    ],
  },
  {
    group: "Memory",
    rows: [
      {
        capability: "Curation memory",
        detail: "Banked triage verdicts, so repeat reads stay cheap. This is our memory of your mail, never your mail itself.",
        values: { free: "30 days", team: "Unlimited", scaling: "Unlimited", enterprise: "Custom" },
      },
      {
        capability: "Retention policy controls",
        values: { free: false, team: true, scaling: true, enterprise: true },
      },
    ],
  },
  {
    group: "Autonomy",
    rows: [
      {
        capability: "Follow-up Manager",
        detail: "Chases what is owed to you and what you owe",
        values: { free: false, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Real-time inbox watch",
        values: { free: false, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Scheduled rules",
        values: { free: false, team: true, scaling: true, enterprise: true },
      },
    ],
  },
  {
    group: "Documents",
    rows: [
      {
        capability: "PDF form filling",
        values: { free: true, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Signature ceremonies",
        detail: "You always sign, never the model",
        values: { free: "3 / month", team: "Unlimited", scaling: "Unlimited", enterprise: "Unlimited" },
      },
    ],
  },
  {
    group: "Team and governance",
    rows: [
      {
        capability: "Sign in with Google",
        values: { free: true, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Shared team rules + mailboxes",
        values: { free: false, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Audit log + admin console",
        values: { free: false, team: true, scaling: true, enterprise: true },
      },
      {
        capability: "Enterprise SSO (Okta, SAML)",
        detail: "Governance add-on, $300/mo",
        values: { free: false, team: "Add-on", scaling: "Add-on", enterprise: true },
      },
      {
        capability: "SSO enforcement",
        detail: "Governance add-on, $300/mo",
        values: { free: false, team: "Add-on", scaling: "Add-on", enterprise: true },
      },
      {
        capability: "Fine-grained RBAC",
        detail: "Governance add-on, $300/mo",
        values: { free: false, team: "Add-on", scaling: "Add-on", enterprise: true },
      },
    ],
  },
  {
    group: "Support and terms",
    rows: [
      {
        capability: "Support",
        values: { free: "Community", team: "Priority", scaling: "Priority + target", enterprise: "Dedicated" },
      },
      {
        capability: "Dedicated Slack / MS Teams channel",
        detail: "Governance add-on, $300/mo",
        values: { free: false, team: "Add-on", scaling: "Add-on", enterprise: true },
      },
      {
        capability: "DPA + security review",
        values: { free: false, team: "DPA", scaling: "DPA", enterprise: true },
      },
      {
        capability: "Uptime SLA",
        values: { free: false, team: false, scaling: false, enterprise: true },
      },
    ],
  },
];
