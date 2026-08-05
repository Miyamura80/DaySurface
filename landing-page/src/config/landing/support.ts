/**
 * Support & contact: the self-serve troubleshooting and the human channels a
 * user reaches when something breaks or they have a question.
 *
 * Data-driven like the rest of the config: this one module feeds the `/support`
 * page, its `/help` and `/contact` aliases, the `/support.md` twin, and the
 * `## Support` section of llms.txt / agents.md / llms-full.txt. Point it at a
 * real inbox and it is live everywhere.
 *
 * Order is deliberate: troubleshooting first (most "issues" are setup, and an
 * agent or user should self-serve before opening a ticket), then the channels.
 */
import { site } from "./site";

/** The support inbox. Kept here so every support surface derives from one value. */
export const supportEmail = "support@daysurface.com";

export interface TroubleshootItem {
  /** Phrased as the symptom a user (or agent) would search for. */
  q: string;
  /**
   * Plain-text answer. NO markdown backticks or link syntax: this string
   * renders verbatim into the HTML page as well as into support.md, so markup
   * that survives to the browser shows up as literal characters (same rule the
   * connect copy follows).
   */
  a: string;
  /** Optional deep link the answer points at (e.g. /connect, the FAQ). */
  href?: string;
  hrefLabel?: string;
}

export interface SupportChannel {
  label: string;
  /** When to use this one, so a user picks the right door the first time. */
  description: string;
  href: string;
  /** Button/label text. */
  cta: string;
  /**
   * "public"  - anyone can read it (GitHub Issues): bugs, features, how-tos.
   * "private" - one-to-one (email): billing, account, anything with your data.
   */
  kind: "public" | "private";
}

export const support: {
  title: string;
  intro: string;
  troubleshooting: TroubleshootItem[];
  channelsHeading: string;
  channels: SupportChannel[];
} = {
  title: "Support & contact",
  intro: `Hit a snag or have a question? Most issues are setup or connection related and are covered below. If that does not sort it, reach a human through one of the channels underneath.`,
  troubleshooting: [
    {
      q: "The server will not connect, or my client rejects the URL",
      a: `${site.name} is a remote streamable-HTTP server, not a local stdio one. Add the endpoint ${site.mcpUrl} exactly, with no trailing path of your own, and use the per-client install link rather than typing the config by hand.`,
      href: "/connect",
      hrefLabel: "Per-client install links",
    },
    {
      q: "Google sign-in or OAuth fails, or keeps asking me to reconnect",
      a: `Connecting opens Google OAuth in your browser; you must grant the Gmail permission for the tools to work. If it loops, disconnect inside your client and reconnect, or revoke the app from your Google account permissions page and start again. There is no API key to paste - auth is entirely in the browser.`,
      href: "https://myaccount.google.com/permissions",
      hrefLabel: "Google account permissions",
    },
    {
      q: "The tools do not appear after I connect",
      a: `Once your client finishes the browser OAuth step, the tools register automatically - list them with the MCP tools/list method (most clients do this for you). If nothing shows, the connection did not complete: remove the server and add it again from the install link.`,
      href: "/connect",
      hrefLabel: "Reconnect",
    },
    {
      q: "Do I need an account, and which URL do I paste?",
      a: `No account, no signup, nothing to install. Paste the endpoint ${site.mcpUrl} into any MCP client; the server name is ${site.serverName}. Full per-client walkthrough is on the connect page.`,
      href: "/connect",
      hrefLabel: "Connect guide",
    },
  ],
  channelsHeading: "Still stuck? Talk to us",
  channels: [
    {
      label: "GitHub Issues",
      description:
        "Bugs, feature requests, and anything technical or reproducible. Public and searchable, so others hit by the same thing find the answer.",
      href: `${site.githubUrl}/issues`,
      cta: "Open an issue",
      kind: "public",
    },
    {
      label: "Email support",
      description:
        "Account, billing, security, or anything involving your data that should not be public. Goes straight to the team.",
      href: `mailto:${supportEmail}`,
      cta: supportEmail,
      kind: "private",
    },
  ],
};
