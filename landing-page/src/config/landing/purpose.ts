/**
 * Plain-language statement of what the product does and what it asks of your
 * Gmail account.
 *
 * This block exists for readers who arrive with zero context - a first-time
 * visitor, an LLM summarising the site, and the Google OAuth verification
 * reviewer, whose checks are (1) the homepage must explain the app's purpose
 * and (2) the name on the homepage must match the app name on the OAuth
 * consent screen. Both are satisfied by rendering this verbatim, high on the
 * page, with the product named as `site.name` - the same string configured as
 * the consent-screen app name.
 *
 * Every machine-readable surface (llms.txt, llms-full.txt, agents.md, the
 * `?mode=agent` view, the WebMCP tools) renders this same block via
 * `src/agent/content.ts`, so the human page and the agent copies cannot
 * disagree about scope, permissions, or data handling.
 *
 * Two copies live outside this build and do not follow this file: the agent
 * skill at `skills/daysurface/SKILL.md` in the repo root (mirrored into
 * `public/.well-known/agent-skills/` by `make sync-skills` - edit the source,
 * never the mirror) and the hand-maintained `public/.well-known/mcp.json`.
 * Update both when this text changes. See landing-page/README.
 *
 * Keep the copy concrete and free of marketing verbs: it doubles as a
 * user-facing disclosure, so every claim here must stay true of the shipped
 * OAuth scopes and match `/privacy`.
 */
import { site } from "./site";

export const purpose: {
  heading: string;
  /** What the product does, in the user's terms. */
  what: string;
  /** Which Gmail permissions that requires, and what happens to the data. */
  permissions: string;
  /** Trailing "See our <link>." sentence, split so text surfaces can inline the URL. */
  privacy: { lead: string; linkText: string; href: string };
} = {
  heading: `What ${site.name} does`,
  what:
    `${site.name} connects your Gmail account to the AI client you already ` +
    `use. Ask what needs your attention and get a ranked inbox. Archive ` +
    `threads and mark them done. Draft a reply in a real composer, edit it ` +
    `yourself, and send it - inside Claude, ChatGPT, or any MCP client.`,
  permissions:
    `To do this, ${site.name} asks permission to read, organise, draft, and ` +
    `send mail in your Gmail account. It never permanently deletes mail. ` +
    `Message content is fetched only when you ask for it and passed to your ` +
    `AI client; we do not store message bodies, and no AI model on our ` +
    `servers reads your mail.`,
  privacy: { lead: "See our", linkText: "privacy policy", href: "/privacy" },
};

/**
 * The whole block as one plain-text paragraph run, with the privacy policy as
 * an absolute URL. Shared by every agent surface so none of them can drift
 * from what the page shows a human.
 */
export function purposeText(origin: string): string {
  const o = origin.replace(/\/+$/, "");
  const { lead, linkText, href } = purpose.privacy;
  return `${purpose.what}\n\n${purpose.permissions} ${lead} ${linkText}: ${o}${href}`;
}
