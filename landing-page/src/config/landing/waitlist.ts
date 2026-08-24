/**
 * Copy + form wiring for the standalone /waitlist page.
 *
 * The page is data-driven like the rest of the site: edit the values here and
 * the whole page re-skins. The one thing you MUST set for the form to do
 * anything real is `endpoint` - see the note on it below.
 */
import { site } from "./site";

export interface WaitlistPerk {
  /** Selects the inline SVG glyph in waitlist.astro (by key). */
  icon: "bolt" | "shield" | "spark";
  title: string;
  body: string;
}

export const waitlist: {
  eyebrow: string;
  headline: string;
  subhead: string;
  /**
   * Where the email form POSTs. Empty by default: the page ships a fully
   * working form UI (validation, inline success/error, no-JS fallback), but
   * with no endpoint it cannot persist anything, so the client script only
   * confirms locally.
   *
   * Wired for Loops (loops.so): create a Form in Loops and paste its custom
   * endpoint here -
   *     https://app.loops.so/api/newsletter-form/<YOUR_FORM_ID>
   * The form submits `email` (and `userGroup` below) as
   * `application/x-www-form-urlencoded`. That endpoint needs NO API key, so it
   * is safe to call from this static page - do not put a Loops API key here or
   * anywhere in the client bundle. Any Loops mailing list attached to the form
   * must be Public. Response is `{ "success": true }`.
   *
   * The same shape (urlencoded `email`, JSON reply) also works with Formspree,
   * Getform, Basin, or a Cloudflare Worker if you switch providers later.
   *
   * TODO: set the Loops form endpoint.
   */
  endpoint: string;
  /**
   * Optional Loops user group tag applied to signups from this form (shows up
   * in Loops for segmenting the waitlist). Sent only when non-empty; ignored by
   * non-Loops backends. Empty string to omit.
   */
  userGroup: string;
  /** Field label + placeholder for the email input. */
  emailLabel: string;
  emailPlaceholder: string;
  /** Submit button copy, resting and while the request is in flight. */
  cta: string;
  ctaPending: string;
  /** Shown after a successful submit (heading + supporting line). */
  successHeading: string;
  successBody: string;
  /**
   * Shown instead of the success copy when no `endpoint` is configured (the
   * shipped default). The form can't persist anything in that state, so this
   * copy must NOT claim the visitor joined a list. Keep it visitor-facing - no
   * config-key or developer jargon - since it renders on the public page until
   * you wire `endpoint`. Once `endpoint` is set, the real success copy is used.
   */
  previewHeading: string;
  previewBody: string;
  /** Generic error line if the POST fails. */
  errorBody: string;
  /** Small reassurance line under the form. */
  finePrint: string;
  /** Caption above the product animation. */
  demoCaption: string;
  perks: WaitlistPerk[];
} = {
  eyebrow: "Early access",
  headline: "Get on the DaySurface waitlist.",
  subhead: `${site.name} brings a ranked inbox, a real reply composer, and fill-and-sign PDFs straight into the AI client you already use. Join the list for priority onboarding and launch updates.`,
  endpoint: "",
  userGroup: "Waitlist",
  emailLabel: "Work email",
  emailPlaceholder: "you@company.com",
  cta: "Join the waitlist",
  ctaPending: "Joining…",
  successHeading: "You're on the list.",
  successBody:
    "Thanks - we'll email you the moment your invite is ready. No spam, just launch news.",
  previewHeading: "Thanks for your interest!",
  previewBody:
    "We're still getting the waitlist set up and aren't collecting signups just yet - please check back soon.",
  errorBody: "Something went wrong. Please try again, or email us and we'll add you by hand.",
  finePrint: "No account needed to try it today - the waitlist is only for early-access perks and updates.",
  demoCaption: "See it in your client",
  perks: [
    {
      icon: "bolt",
      title: "Priority onboarding",
      body: "Skip the line when hosted early access opens, with setup help for your client of choice.",
    },
    {
      icon: "spark",
      title: "Shape the roadmap",
      body: "Early members get a direct line to influence which Gmail workflows we build next.",
    },
    {
      icon: "shield",
      title: "Yours to leave",
      body: "One email field, no account, no card. Unsubscribe in a click whenever you want.",
    },
  ],
};
