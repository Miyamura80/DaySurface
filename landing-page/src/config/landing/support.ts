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
  /** Link / button text. */
  cta: string;
  href: string;
  /** Terse routing hint - which door this is. Keep it to a few words. */
  blurb: string;
  /**
   * Reused mark for the CTA: a logo in public/logos/, or the built-in "mail"
   * glyph. Only rendered for the `primary` channel (the one drawn as a button);
   * omit it on secondary channels, which render as plain text links.
   */
  icon?: "github" | "mail";
  /**
   * Exactly one channel is `primary`: it renders as the single accent CTA.
   * Every other channel is a quiet secondary link (see the design rule "one
   * CTA per view"). DaySurface serves end users, so email leads and GitHub is
   * the developer fallback.
   */
  primary: boolean;
}

export const support: {
  title: string;
  issuesHeading: string;
  troubleshooting: TroubleshootItem[];
  channelsHeading: string;
  channels: SupportChannel[];
} = {
  title: "Support & contact",
  issuesHeading: "Common issues",
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
  ],
  channelsHeading: "Still stuck?",
  channels: [
    {
      cta: "Email support",
      href: `mailto:${supportEmail}`,
      blurb: "Billing, account, or anything private",
      icon: "mail",
      primary: true,
    },
    {
      cta: "Open a GitHub issue",
      href: `${site.githubUrl}/issues`,
      blurb: "Public bugs and feature requests",
      primary: false,
    },
  ],
};

/**
 * Alias routes that serve the /support content: /help and /contact. Kept as one
 * list so the HTML pages, their markdown twins (help.md.ts / contact.md.ts), and
 * the server's noindex set can't drift out of sync - the same reason the connect
 * intent aliases live in one `connectAliases`.
 */
export const supportAliases = ["help", "contact"] as const;
