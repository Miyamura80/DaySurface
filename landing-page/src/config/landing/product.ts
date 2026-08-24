/**
 * Copy for the /product page: how DaySurface works and why it beats a plain
 * Gmail-API wrapper.
 *
 * The homepage answers one question - what DaySurface is and who it's for. This
 * page answers the next two: *how* it works and *why* it's better. It leans on
 * config that already exists rather than restating it: the three differentiator
 * `pillars` and their full prose `body` live in ./comparison, and the composer /
 * triage / signing outcomes live in `features` (./hero). This module only holds
 * what is unique to the page - its hero and the step-by-step flow - so there is
 * one source of truth per claim.
 */
import { site } from "./site";

export interface ProductStep {
  title: string;
  body: string;
}

export const product: {
  /** <title> and meta description for the page + its markdown twin. */
  metaTitle: string;
  metaDescription: string;
  /** On-page hero. */
  heading: string;
  subhead: string;
  /** "How it works" - the load-bearing sequence, rendered as an ordered list. */
  howHeading: string;
  howSubhead: string;
  steps: ProductStep[];
  /** "Why it's better" section intro; the pillars themselves come from ./comparison. */
  whyHeading: string;
  whySubhead: string;
  /** "What you can do" section intro; the items come from `features` in ./hero. */
  capabilitiesHeading: string;
  capabilitiesSubhead: string;
} = {
  metaTitle: `How ${site.name} works`,
  metaDescription: `${site.name} renders a real email composer and a ranked inbox inside your chat client, exposes the same tools over CLI and HTTP, and is open source to self-host. Here's how it works and why it's different.`,
  heading: "An inbox you drive from inside the chat.",
  subhead:
    "Most Gmail MCP servers hand your agent a wall of JSON. DaySurface renders the actual product - a composer you edit drafts in and a ranked inbox you triage - right where your agent already is.",
  howHeading: "How it works",
  howSubhead:
    "No account to create and nothing to install. Adding one URL to your client is the whole setup.",
  steps: [
    {
      title: "Add one URL to your client",
      body: "DaySurface is a remote MCP server over streamable HTTP. Paste its endpoint into Claude, ChatGPT, Cursor, VS Code, Goose, or any MCP client - there is no signup, no download, and no API key to manage.",
    },
    {
      title: "Sign in to Google, in the client",
      body: "Authorise with OAuth in the browser the first time you call a tool. A single minimal Gmail scope, encrypted token storage, and a disconnect that revokes the token with Google and erases it from our database.",
    },
    {
      title: "Ask, and your agent triages and drafts",
      body: "Ask what needs your attention and get a ranked inbox, not a raw search dump. Ask it to reply and the draft lands in a real composer. Your agent does the reading and the first pass; the verdicts are banked against each thread's history so the next ask only re-reads what changed.",
    },
    {
      title: "You edit and send",
      body: "Every action lands somewhere you can edit. Recipients, subject, body and attachments are yours to change before anything leaves your account - and signing a PDF is a step only you can complete, by typing your own name.",
    },
  ],
  whyHeading: "Why it's different",
  whySubhead:
    "Three things set DaySurface apart from a Gmail-API wrapper. Each is a deliberate trade of breadth for depth on email.",
  capabilitiesHeading: "What you can do",
  capabilitiesSubhead:
    "Three end-user outcomes, each rendered inside the chat rather than returned as JSON.",
} as const;
