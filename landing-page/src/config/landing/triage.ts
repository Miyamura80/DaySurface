/**
 * Content for `/ai-email-triage`.
 *
 * The brief for this page was "saving hours per week with mail". That is a
 * benefit, not a query - nobody searches it - so the page is titled for the
 * thing people do search ("ai email triage", "email triage automation") and the
 * hours argument became the page's argument rather than its headline.
 *
 * NOTHING HERE MAY CARRY A NUMBER. There is no usage study, no measured cohort
 * and no customer telemetry behind this page, so every claim is mechanical: it
 * says what the software does and what step that removes, and lets the reader do
 * their own arithmetic. If someone later wants "saves N hours a week" on this
 * page, that sentence needs a measurement behind it first, not a plausible
 * estimate. The same rule is why the FAQ answers "how much time will this save"
 * with a refusal rather than a range.
 */
import type { FaqItem } from "./content";

/** A step of the manual loop, paired with what replaces it. */
export interface TriageStep {
  /** What the manual version costs you. */
  manual: string;
  /** What happens instead. Mechanism, not outcome. */
  instead: string;
}

/** One reason generic "AI email" tools fall down. */
export interface TriageGap {
  title: string;
  body: string;
}

export const triage: {
  heading: string;
  subhead: string;
  lede: string;
  loopHeading: string;
  loopIntro: string;
  steps: TriageStep[];
  gapsHeading: string;
  gaps: TriageGap[];
  honestyHeading: string;
  honesty: string;
  faqHeading: string;
  faq: FaqItem[];
} = {
  heading: "AI email triage",
  subhead: "The time goes into deciding, not reading. That is the part worth automating.",
  lede: "Most of what an inbox costs you is not reading messages. It is the loop around them: opening a thread to find out whether it matters, deciding it does not, closing it, and doing that again a hundred times before you reach the four that needed you. Triage is the act of collapsing that loop. This page is about what an AI agent can actually take off you there, and what it cannot.",

  loopHeading: "Where the time actually goes",
  loopIntro:
    "Each row is a step of the manual loop and what replaces it when your agent can reach the mailbox directly. No step here is hypothetical - each maps to a tool the server exposes.",
  steps: [
    {
      manual: "Open each unread thread to work out whether it needs you.",
      instead:
        "The inbox arrives ranked, as an interactive dashboard inside the chat. You are reading a shortlist rather than a list.",
    },
    {
      manual: "Re-read a long thread to reconstruct what was agreed.",
      instead:
        "The agent reads the full thread server-side and answers questions about it, so you never load the whole history to find one commitment.",
    },
    {
      manual: "Switch to the mail client to write a reply, then switch back.",
      instead:
        "Replies open in an editable composer in the conversation. You correct the draft in place and send from there.",
    },
    {
      manual: "Download a PDF attachment, fill it in another app, re-attach it.",
      instead:
        "Forms are filled from the thread itself. Signing stays yours - the tool will not type a signature for you.",
    },
    {
      manual: "Search for the thread you half-remember, by guessing keywords.",
      instead:
        "Search runs across your mail server-side and returns threads, so the agent works from your actual inbox rather than what fits in its context.",
    },
  ],

  gapsHeading: "Why most 'AI for email' tools do not help",
  gaps: [
    {
      title: "They summarise instead of deciding",
      body: "A summary of forty threads is still forty things to read. The saving only appears when something ranks them and you can act on the top of the list without opening the rest.",
    },
    {
      title: "They hand the model raw JSON",
      body: "Most Gmail MCP servers return API payloads and stop. The model then narrates them back at you, which adds a step rather than removing one. An interactive surface you can act in is what turns a tool call into a decision.",
    },
    {
      title: "They act without showing you",
      body: "Anything that sends on the model's judgement alone buys speed with risk you did not agree to. A draft you approve is slower per message and far cheaper the first time the model is wrong.",
    },
    {
      title: "They live outside the place you are already working",
      body: "A separate triage app is another context switch, which is the cost you were trying to remove. Running inside the assistant you already have open is the point.",
    },
  ],

  honestyHeading: "What we are not going to tell you",
  honesty:
    "We are not going to put a number on this. We have not run a time study, and any figure we published would be a guess dressed as evidence. What the page above describes is mechanical and checkable: these are the steps the software removes, and you know better than we do what those steps currently cost you. If you want the honest version of the pitch, it is that triage is where agents are genuinely good, and reading your mail for you is not the same as deciding what deserves you.",

  faqHeading: "Questions",
  faq: [
    {
      q: "How much time will this actually save me?",
      a: "We do not know, and we are not going to invent a figure. It depends on how much mail you get and how much of it needs you. What we can say precisely is which steps come out of the loop - they are listed above - so you can weigh that against your own week.",
    },
    {
      q: "Does it send email on my behalf without asking?",
      a: "No. Replies open in a composer you edit and send yourself. The design assumption is that an agent will occasionally be wrong about tone, recipient or timing, and that a draft you approve costs far less than a send you have to retract.",
    },
    {
      q: "Which assistant does this run in?",
      a: "Any MCP client. Claude and ChatGPT have their own setup guides, and the install picker covers Cursor, VS Code, Goose, Claude Code, Cline, Zed and Windsurf.",
    },
    {
      q: "What does it need access to?",
      a: "The Google scopes you approve at sign-in, and nothing else. You review them on the consent screen and can revoke them from your Google account afterwards.",
    },
    {
      q: "Can I self-host it?",
      a: "Yes. It is MIT-licensed, and the same tools are exposed over a CLI and a plain HTTP API as well as MCP, so you can point a client at your own deployment.",
    },
  ],
};
