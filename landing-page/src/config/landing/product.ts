/**
 * Copy for the /product page: how DaySurface works and why it's better.
 *
 * The homepage answers one question - what DaySurface is and who it's for. This
 * page answers the next two: *how* it works and *why* it's better. It stays
 * deliberately light on prose: the hero reuses the homepage ChatMock (the live
 * client-toggle inbox demo), and "why it's different" reuses the PillarCards
 * diagrams. This module only holds the hero copy and the one-line setup steps -
 * the full differentiator prose lives in ./comparison and on the /vs pages.
 */
import { site } from "./site";

export interface ProductStep {
  title: string;
  body: string;
}

export const product: {
  metaTitle: string;
  metaDescription: string;
  heading: string;
  subhead: string;
  /** "How it works" - the setup sequence, one line each. */
  howHeading: string;
  steps: ProductStep[];
  /** "Why it's different" intro; the pillar diagrams come from ./comparison. */
  whyHeading: string;
} = {
  metaTitle: `How ${site.name} works`,
  metaDescription: `${site.name} renders a real email composer and a ranked inbox inside your chat client, exposes the same tools over CLI and HTTP, and is open source to self-host. Here's how it works and why it's different.`,
  heading: "An inbox you drive from inside the chat.",
  subhead:
    "Most Gmail MCP servers hand your agent a wall of JSON. DaySurface renders the product - a composer and a ranked inbox - right where your agent already is.",
  howHeading: "How it works",
  steps: [
    {
      title: "Add one URL",
      body: "Paste the endpoint into any MCP client that speaks streamable HTTP. No account, no install, no API key.",
    },
    {
      title: "Sign in to Google",
      body: "OAuth in the browser, one minimal Gmail scope. Disconnect drops the stored connection and asks Google to revoke access.",
    },
    {
      title: "Ask",
      body: "Your agent ranks the inbox and drafts replies - banked against each thread's history.",
    },
    {
      title: "Edit and send",
      body: "Every draft opens where you can change it. Nothing leaves until you press send.",
    },
  ],
  whyHeading: "Why it's different",
} as const;
