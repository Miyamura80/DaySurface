/**
 * Copy for the /story page: why DaySurface was built.
 *
 * This is deliberately NOT /about. `/about` is a plain-language, prose-only page
 * written for a Google OAuth verification reviewer - it states what the app does
 * with a user's Gmail account and which permissions it asks for, and it must
 * stay that way. This page carries the founding narrative instead: the problem,
 * the turning point, and where it's going.
 *
 * This is the founder's own telling: the motivation was wanting agents to run
 * a personal inbox without getting locked into any single assistant to do it.
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
  metaDescription: `The story behind ${site.name}: I wanted my agents to run my email, and I didn't want to be locked into any one of them. So I built a Gmail server that's portable across every agent - open source, and yours to host.`,
  eyebrow: "Our story",
  heading: "Why we built DaySurface",
  lede: "I wanted my agents to run my email. I didn't want to marry one of them to do it.",
  sections: [
    {
      heading: "The itch",
      paragraphs: [
        "I have too much email, and I have agents that are finally good enough to help with it. The obvious move was to just let them - read what came in, tell me what actually needs me, draft the replies I keep putting off.",
        "Every option that could do that came bolted to one assistant. Set it up inside that assistant, learn its way of doing things, and your inbox now lives there. That felt backwards. My mail is mine; the agent is a tool I should be free to swap.",
      ],
    },
    {
      heading: "Portable on purpose",
      paragraphs: [
        "So the first rule was no lock-in. DaySurface is a Model Context Protocol server, which means any agent that speaks MCP can drive it - Claude today, something else next month, two of them at once if you like. Move setups and your email tooling comes with you. Nothing to re-learn, nothing stranded.",
        "The same tools also run over a plain CLI and an HTTP API, not just MCP. One codebase behind all three, so a script, your shell, and your agent all reach the exact same inbox with the exact same behaviour.",
      ],
    },
    {
      heading: "Yours to run",
      paragraphs: [
        "Portability only counts if the thing underneath is yours. So the whole server is open source and ships to self-host with your own Google credentials - your mail never routes through a service you can't inspect or replace.",
        "That's the whole bet: let your agents actually work your inbox, and never let any single one of them own it. It's early and the roadmap is public. If you've wanted the same thing, come try it.",
      ],
    },
  ],
  signoff: "Eito, DaySurface",
} as const;
