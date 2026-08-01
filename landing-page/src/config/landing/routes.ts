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
 * Paths answered with a 301 to somewhere else on this origin.
 *
 * `/developers` is the URL a human or an agent guesses for developer
 * documentation. It has no page here, so it used to 404; the documentation it
 * is asking for is the docs app, proxied onto this origin at `/docs` (see
 * `docs-proxy.ts`).
 *
 * A redirect rather than the connect-alias treatment - serving the content at a
 * 200 - because the target lives in another service. There is no body to
 * inline, and 301 is what moves any link equity `/developers` accumulates onto
 * the canonical `/docs` URL instead of splitting it.
 *
 * Keys must be normalised: no trailing slash, no query string. Both are
 * stripped before lookup, and the query is carried through to the target.
 */
export const permanentRedirects: Readonly<Record<string, string>> = {
  "/developers": "/docs",
};
