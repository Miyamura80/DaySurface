import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  // The docs are a section of daysurface.com, not their own domain. `basePath`
  // namespaces EVERYTHING this app serves - pages, `/_next` assets, `/api`,
  // `/sitemap.xml`, `/robots.txt` - under a single `/docs` prefix, so the apex
  // needs exactly one proxy rule (see `landing-page/server.ts`) and nothing can
  // collide with a landing-page route.
  //
  // Route dirs must NOT repeat the segment: `app/[lang]/(docs)/[[...slug]]`
  // serves `/docs/<slug>`, because basePath supplies the `/docs` itself.
  basePath: '/docs',
  async rewrites() {
    // Sources and destinations are relative to basePath; Next prepends it.
    return [
      {
        source: '/:lang/:path*.mdx',
        destination: '/llms.mdx/:lang/:path*',
      },
      {
        source: '/:path*.mdx',
        destination: '/llms.mdx/en/:path*',
      },
    ];
  },
};

export default withMDX(config);
