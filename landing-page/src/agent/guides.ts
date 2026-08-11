/**
 * Markdown twins for the guide pages: connect-gmail-to-<client>.md and
 * ai-email-triage.md.
 *
 * Both HTML pages carry their substance in structures a markdown converter
 * handles badly - numbered steps as `<li>` with nested headings, a two-column
 * table, and FAQ answers hidden inside `details`. The answers are exactly the
 * part an answer engine wants to quote, so they are spelled out here rather than
 * left to a converter to recover.
 */
import {
  site,
  clientGuides,
  connect,
  triage,
  guideMdPath,
  type ClientGuide,
} from "../config/landing";
import { faqSection, trimSlash } from "./_shared";

/**
 * Takes the guide itself, not a slug to look up.
 *
 * The route hands it straight through from `getStaticPaths` props, so there is
 * no miss to handle. Looking it up here instead meant a `# Not found` body
 * served with HTTP 200 and `Content-Type: text/markdown` for a state the static
 * build makes unreachable - a fallback that could only ever have lied about
 * having found something.
 */
export function buildClientGuideMd(origin: string, guide: ClientGuide): string {
  const o = trimSlash(origin);

  const others = clientGuides
    .filter((g) => g.slug !== guide.slug)
    .map((g) => `- Connect Gmail to ${g.clientName}: ${o}${guideMdPath(g.slug)}`)
    .join("\n");

  return `# ${guide.heading}

> ${guide.subhead}

${guide.lede}

## The server URL

\`\`\`
${connect.mcpUrl}
\`\`\`

Transport is streamable HTTP. Auth is OAuth in the browser - there is no key to
paste and no account to create.

## Before you start

${guide.prerequisites.map((p) => `- ${p}`).join("\n")}

## Add ${site.name} to ${guide.clientName}

${guide.steps.map((s, i) => `${i + 1}. **${s.title}** - ${s.body}`).join("\n")}

## What ${guide.clientName} can do once it is connected

${guide.capabilities.map((c) => `### ${c.title}\n${c.body}`).join("\n\n")}

## Questions

${faqSection(guide.faq)}

## More

${others}
- Triage argument: ${o}/ai-email-triage.md
- Every client's install path: ${o}/connect.md
- Setup reference: ${site.docsUrl}/mcp/setup
- Full description for LLMs: ${o}/llms-full.txt
`;
}

export function buildTriageMd(origin: string): string {
  const o = trimSlash(origin);

  const loopRows = triage.steps
    .map((r) => `| ${r.manual} | ${r.instead} |`)
    .join("\n");

  return `# ${triage.heading}

> ${triage.subhead}

${triage.lede}

## ${triage.loopHeading}

${triage.loopIntro}

| Doing it by hand | With ${site.name} |
| --- | --- |
${loopRows}

## ${triage.gapsHeading}

${triage.gaps.map((g, i) => `### ${i + 1}. ${g.title}\n${g.body}`).join("\n\n")}

## ${triage.honestyHeading}

${triage.honesty}

## ${triage.faqHeading}

${faqSection(triage.faq)}

## More

${clientGuides
  .map((g) => `- Connect Gmail to ${g.clientName}: ${o}${guideMdPath(g.slug)}`)
  .join("\n")}
- Gmail MCP alternatives: ${o}/compare.md
- Get connected: ${o}/connect.md
- Full description for LLMs: ${o}/llms-full.txt
`;
}
