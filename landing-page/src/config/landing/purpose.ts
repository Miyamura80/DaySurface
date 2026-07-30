/**
 * "What DaySurface does" - the plain-language purpose block on the home page.
 *
 * Written for two readers at once: a first-time visitor, and a Google OAuth
 * app reviewer. Google's verification rejects a home page that does not state
 * the app's name, explain in plain language what the app is for, say which
 * Google user data it requests and why, and link to the privacy policy - so
 * this section is a compliance surface, not decoration. Two invariants:
 *
 * 1. The app name here is `site.name`, which must stay identical to the "App
 *    name" on the OAuth consent screen.
 * 2. `scopes` must list exactly the scopes the server actually requests, with
 *    the same wording as the "Data we access" section of /privacy.
 */
import { site, legal } from "./site";

export interface PurposePoint {
  label: string;
  body: string;
}

/** One requested OAuth scope and the user-facing reason it is needed. */
export interface PurposeScope {
  /** Human-readable scope name, as shown on Google's consent screen. */
  name: string;
  /** The raw scope string (or a short form of it) for developers. */
  scope: string;
  why: string;
}

export const purpose: {
  heading: string;
  lead: string;
  points: PurposePoint[];
  scopesHeading: string;
  scopes: PurposeScope[];
  consent: string;
  links: { label: string; href: string; external?: boolean }[];
} = {
  heading: `What ${site.name} does`,
  lead:
    `${site.name} is a Gmail assistant that lives in the AI client you already use. ` +
    `It is a Model Context Protocol (MCP) server: you sign in with Google once and add the ` +
    `${site.name} server to Claude, ChatGPT, Cursor, VS Code, or any other MCP client. That ` +
    `assistant can then work your inbox with you - ranking the threads that actually need a ` +
    `reply, writing responses into a real Gmail draft you edit before it goes anywhere, and ` +
    `filling in PDF attachments for you to sign.`,
  points: [
    {
      label: "Who it is for",
      body:
        "People who run their day out of Gmail and already work with an AI assistant, and " +
        "want it to handle triage and first drafts instead of copying mail back and forth " +
        "into a chat window.",
    },
    {
      label: "What it does with your mail",
      body:
        "Reads and searches the threads your assistant needs to answer your request, applies " +
        "labels, archives, and creates drafts. Every action is one you asked for in your AI " +
        "client, and sending stays your decision - the draft opens in a composer you edit " +
        "and send yourself.",
    },
    {
      label: "Who operates it",
      body:
        `${site.name} is operated by ${legal.operator}, a company registered in ` +
        `${legal.jurisdiction}. Support and data requests go to ${legal.supportEmail}. The ` +
        `server is open source, so you can read exactly what it does with your mailbox - or ` +
        `run your own copy of it.`,
    },
  ],
  scopesHeading: "The Google access it asks for",
  scopes: [
    {
      name: "Your Google account identity",
      scope: "openid, email",
      why:
        "Identifies which Google account is connected, so your session can be recognised " +
        "and revoked. No profile data beyond your email address is requested.",
    },
    {
      name: "Read, compose, send, and manage your Gmail",
      scope: "gmail.modify",
      why:
        "Powers the features above: reading and searching threads to answer your questions, " +
        "labelling and archiving during triage, and creating and sending the drafts you " +
        "approve. It does not permit permanent deletion of your mail.",
    },
  ],
  consent:
    `Access begins only after you grant it on Google's own consent screen, happens only in ` +
    `response to requests you make in your AI client, and can be revoked at any time from ` +
    `your Google account permissions. ${site.name}'s use and transfer of information ` +
    `received from Google APIs adheres to the Google API Services User Data Policy, ` +
    `including the Limited Use requirements. Your mail is never used to train AI models, and ` +
    `no AI inference on it happens on our servers.`,
  links: [
    { label: "Privacy policy", href: "/privacy" },
    { label: "Terms of service", href: "/terms" },
    {
      label: "Revoke access at Google",
      href: "https://myaccount.google.com/permissions",
      external: true,
    },
  ],
};
