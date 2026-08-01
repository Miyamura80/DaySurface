import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/**
 * URL shapes readers and agents guess for pages that live somewhere else.
 *
 * Each entry is an alias slug mapped to the canonical slug it should land on,
 * both relative to `/docs`. They resolve as permanent redirects rather than as
 * second copies of the page, so there stays exactly one indexable URL per page
 * and no content to keep in sync.
 *
 * Add an entry when a guessable path 404s, not a page.
 */
const DOC_ALIASES = {
  // The sidebar calls it "Quick Start"; "getting started" is what people type.
  'getting-started': 'quickstart',
  // Flattened spelling of the nested page - the shape a reader who has only
  // seen `/docs/api` would extrapolate.
  'api-authentication': 'api/authentication',
};

/**
 * Locale segments the docs serve, so `/:lang/docs/<alias>` matches only a real
 * language and not any first path segment. Must match `languages` in
 * `lib/i18n.ts`.
 */
const LOCALES = 'en|zh|es|ja';

/**
 * Redirects for every alias, in both the bare (English, `hideLocale:
 * "default-locale"`) and locale-prefixed forms.
 *
 * The `.mdx` twins are listed explicitly because `redirects()` runs before
 * `rewrites()`: without them `/docs/getting-started.mdx` would be rewritten to
 * an `/llms.mdx/...` path that has no page behind it, and 404 for exactly the
 * agent-facing clients the twin exists to serve.
 */
function aliasRedirects() {
  return Object.entries(DOC_ALIASES).flatMap(([alias, canonical]) => [
    { source: `/docs/${alias}`, destination: `/docs/${canonical}`, permanent: true },
    {
      source: `/docs/${alias}.mdx`,
      destination: `/docs/${canonical}.mdx`,
      permanent: true,
    },
    {
      source: `/:lang(${LOCALES})/docs/${alias}`,
      destination: `/:lang/docs/${canonical}`,
      permanent: true,
    },
    {
      source: `/:lang(${LOCALES})/docs/${alias}.mdx`,
      destination: `/:lang/docs/${canonical}.mdx`,
      permanent: true,
    },
  ]);
}

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  async redirects() {
    return aliasRedirects();
  },
  async rewrites() {
    return [
      {
        source: '/:lang/docs/:path*.mdx',
        destination: '/llms.mdx/:lang/docs/:path*',
      },
      {
        source: '/docs/:path*.mdx',
        destination: '/llms.mdx/en/docs/:path*',
      },
    ];
  },
};

export default withMDX(config);
