import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/**
 * URL shapes readers and agents guess for pages that live somewhere else.
 *
 * Keys and values are literal slugs relative to `/docs` - they interpolate into
 * path-to-regexp patterns below, so a `:` or `(` in one would fail the build
 * rather than route oddly. Each resolves as a permanent redirect rather than a
 * second copy of the page, so there stays exactly one indexable URL per page
 * and no content to keep in sync.
 *
 * Add an entry when a guessable path 404s. Never add one whose key is a slug a
 * real page might later want: `redirects()` is matched ahead of the filesystem,
 * so the alias would shadow that page permanently and silently.
 */
const DOC_ALIASES = {
  // The sidebar calls it "Quick Start"; "getting started" is what people type.
  'getting-started': 'quickstart',
  // Flattened spelling of the nested page - the shape a reader who has only
  // seen `/docs/api` would extrapolate.
  'api-authentication': 'api/authentication',
};

/**
 * A redirect per alias, plus one for its `.mdx` twin.
 *
 * The twin needs its own entry because the bare rule's `source` does not match
 * a URL ending in `.mdx`. Left alone, `/docs/getting-started.mdx` would fall
 * through to the `.mdx` rewrite below, resolve to an `/llms.mdx/...` path with
 * no page behind it, and 404 for exactly the agent clients the twin exists to
 * serve. Redirects are matched before rewrites, so the entry lands first.
 *
 * Only the bare `/docs/...` forms are aliased. A locale-prefixed guess
 * (`/zh/docs/getting-started`) is not a shape anyone types, and nothing below
 * `index` is translated for it to land on.
 */
function aliasRedirects() {
  return Object.entries(DOC_ALIASES).flatMap(([alias, canonical]) => [
    { source: `/docs/${alias}`, destination: `/docs/${canonical}`, permanent: true },
    {
      source: `/docs/${alias}.mdx`,
      destination: `/docs/${canonical}.mdx`,
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
