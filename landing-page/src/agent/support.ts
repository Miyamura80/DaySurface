/**
 * The support surface: `/support.md`, the markdown twin of the /support page.
 *
 * An agent acting for a user who is stuck asks "how do I get help / who do I
 * contact" - this is the document that answers it in one fetch, with the
 * self-serve fixes first and the human channels after. Built from the same
 * `support` config the HTML page renders, so the two never disagree.
 */
import { site, support, supportEmail } from "../config/landing";
import { trimSlash } from "./_shared";

/** support.md - troubleshooting plus how to reach a human. */
export function buildSupportMd(origin: string): string {
  const o = trimSlash(origin);

  const troubleshooting = support.troubleshooting
    .map((item) => {
      const link = item.href
        ? `\n  ${item.hrefLabel ?? "More"}: ${item.href.startsWith("http") ? item.href : o + item.href}`
        : "";
      return `### ${item.q}\n${item.a}${link}`;
    })
    .join("\n\n");

  // Primary first, so the fetch order matches the page's visual priority.
  const ordered = [...support.channels].sort(
    (a, b) => Number(b.primary) - Number(a.primary),
  );
  const channels = ordered
    .map((c) => {
      const target = c.href.startsWith("mailto:") ? supportEmail : c.href;
      return `### ${c.cta}\n${c.blurb}.\n  ${c.href.startsWith("mailto:") ? `Email: ${supportEmail}` : target}`;
    })
    .join("\n\n");

  return `# ${support.title}

## ${support.issuesHeading}

${troubleshooting}

More questions: ${o}/#faq and the docs at ${site.docsUrl}.

## ${support.channelsHeading}

${channels}

## More

- Connect guide (every client): ${o}/connect.md
- Full description for LLMs: ${o}/llms-full.txt
- Source: ${site.githubUrl}
`;
}
