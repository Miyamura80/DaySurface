/**
 * Copy for the /story page: why DaySurface was built.
 *
 * This is deliberately NOT /about. `/about` is a plain-language, prose-only page
 * written for a Google OAuth verification reviewer - it states what the app does
 * with a user's Gmail account and which permissions it asks for, and it must
 * stay that way. This page carries the founding narrative instead: the problem,
 * the turning point, and where it's going.
 *
 * TODO: this is a faithful telling of the *product* thesis, not a personal one.
 * Replace the placeholders below with the real founder's voice - the actual
 * moment, the actual name - the day you have it. A specific story beats a
 * defensible one.
 */
import { site } from "./site";

export interface StorySection {
  heading: string;
  /** One or more paragraphs. Rendered in order, each as its own <p>. */
  paragraphs: string[];
}

export const story: {
  metaTitle: string;
  metaDescription: string;
  /** Hero. */
  eyebrow: string;
  heading: string;
  lede: string;
  sections: StorySection[];
  /** Closing signature line under the last section. */
  signoff: string;
} = {
  metaTitle: `Why we built ${site.name}`,
  metaDescription: `The story behind ${site.name}: why email tools for agents kept returning walls of JSON, and why we built a Gmail product you drive from inside the chat - open source, and yours to host.`,
  eyebrow: "Our story",
  heading: "Why we built DaySurface",
  lede: "Agents got good at email fast. The tools they were handed did not.",
  sections: [
    {
      heading: "The wall of JSON",
      paragraphs: [
        "Every Gmail integration we tried gave an agent the same thing: a search endpoint and a send endpoint, and a wall of JSON in between. The agent could technically read your mail and technically draft a reply - but you never saw the draft until it was already sent, and \"what needs my attention?\" meant re-running a search from scratch every single time.",
        "It worked in a demo and fell apart the moment you trusted it with a real inbox. The problem was never the model. It was that email had been wrapped, not built.",
      ],
    },
    {
      heading: "Email is a product, not an endpoint",
      paragraphs: [
        "So we built the thing we actually wanted: a real composer you edit drafts in, and a ranked inbox you triage - rendered inside the chat, not returned as a blob for the agent to describe back to you. Your agent does the reading and the first pass. You keep the last word, because nothing leaves your account until you press send.",
        "Going deep on one product meant saying no to breadth. DaySurface does Gmail, and only Gmail, on purpose - a single minimal scope instead of a gateway holding keys to your whole digital life.",
      ],
    },
    {
      heading: "Yours to run",
      paragraphs: [
        "The last decision was the easiest. Your mail should never route through a proprietary service you can't inspect, so the whole server is open source and ships to self-host with your own OAuth credentials. The same tools run over a CLI and a plain HTTP API, not just MCP - one codebase, three interfaces, no behaviour that drifts between them.",
        "That is the whole bet: email done deeply, in the open, where you can see it. We're still early, and the roadmap is public. If the wall of JSON ever frustrated you too, we'd love for you to try it.",
      ],
    },
  ],
  // TODO: replace with the real founder / team signature.
  signoff: "The DaySurface team",
} as const;
