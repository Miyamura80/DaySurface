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
   * confirms locally. Point this at any static-friendly form backend that
   * accepts a POST and can return JSON when sent `Accept: application/json`
   * (Formspree, Getform, Basin, Web3Forms, a Cloudflare Worker, ...). The
   * form sends an `email` field (and a honeypot `company` field the backend
   * should reject when non-empty).
   *
   * TODO: set the real waitlist form endpoint.
   */
  endpoint: string;
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
   * Shown instead of the success copy when no `endpoint` is configured. The
   * form can't persist anything in that state, so it must NOT claim the visitor
   * joined a list - this copy says the form is a preview until it's wired. Once
   * `endpoint` is set, the real success copy above is used.
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
  emailLabel: "Work email",
  emailPlaceholder: "you@company.com",
  cta: "Join the waitlist",
  ctaPending: "Joining…",
  successHeading: "You're on the list.",
  successBody:
    "Thanks - we'll email you the moment your invite is ready. No spam, just launch news.",
  previewHeading: "Preview - not collecting yet.",
  previewBody:
    "This waitlist form isn't wired to a backend. Set `waitlist.endpoint` in the site config to start capturing signups.",
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
