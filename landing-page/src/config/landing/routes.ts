/**
 * Route data: URLs the site answers that aren't a page in their own right.
 */

/**
 * Paths that all answer the connect question, served by `pages/[intent].astro`.
 *
 * An agent asked to "create an account" probes these before it reads anything,
 * and the SPA fallback used to answer every one of them with a 200 and the
 * homepage - indistinguishable from a real page, so the agent concluded the
 * site was client-rendered. They now serve the connect content itself,
 * canonicalised to `/connect` and marked noindex so only one URL is indexed.
 *
 * `"connect"` is not in this list - it is the canonical route, prepended by
 * `[intent].astro`, which is also what stops these from being a second copy of
 * the page.
 *
 * Adding a name that collides with a real page (`api`, `compare`, `pricing`)
 * would let the static route silently shadow it. `agent-journey.test.ts`
 * iterates this whole list and asserts each entry serves the connect content,
 * which is what catches that.
 */
export const connectAliases = [
  "signup",
  "sign-up",
  "register",
  "login",
  "sign-in",
  "account",
  "get-started",
  "start",
  "install",
  "setup",
  "onboarding",
] as const;

/** The canonical connect route, plus every alias that resolves to it. */
export const connectRoutes = ["connect", ...connectAliases] as const;

/**
 * The path for one client's setup guide, and its markdown twin.
 *
 * Both were hand-concatenated in nine places across five files - the route, the
 * two feeds, `llms.txt` and the markdown builder - which is how the site ends up
 * with one caller quietly disagreeing with the rest about a trailing slash. That
 * exact bug class was just fixed in the docs' absolute links; this stops it
 * being reintroduced here.
 *
 * No trailing slash: Astro builds `<path>/index.html` and canonicalises to the
 * slashed form itself, and `sitemap.xml.ts` appends the slash at feed-build
 * time. Emitting it here as well would double it.
 */
export function guidePath(slug: string): string {
  return `/connect-gmail-to-${slug}`;
}

/** The `.md` twin of {@link guidePath}, served to `Accept: text/markdown`. */
export function guideMdPath(slug: string): string {
  return `${guidePath(slug)}.md`;
}
