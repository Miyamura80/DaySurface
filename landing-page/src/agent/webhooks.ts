/**
 * gmail-webhooks.md - the /gmail-webhooks page as markdown.
 *
 * The HTML page carries its comparison as a table and its FAQ inside `details`
 * elements. Both survive a markdown converter poorly, and the answers are the
 * part an answer engine most wants to quote, so they are spelled out here.
 */
import { site, webhooks } from "../config/landing";
import { trimSlash } from "./_shared";

export function buildGmailWebhooksMd(origin: string): string {
  const o = trimSlash(origin);

  const approachRows = webhooks.approaches
    .map((r) => `| ${r.capability} | ${r.polling} | ${r.pubsub} | ${r.us} |`)
    .join("\n");

  return `# ${webhooks.heading}

> ${webhooks.subhead}

${webhooks.lede}

## ${webhooks.gapsHeading}

${webhooks.gaps.map((g, i) => `### ${i + 1}. ${g.title}\n${g.body}`).join("\n\n")}

## ${webhooks.approachHeading}

| Capability | Poll the API | Pub/Sub push, direct | ${site.name} |
| --- | --- | --- | --- |
${approachRows}

## Verifying a delivery

Every POST carries \`X-Webhook-Signature\` (\`sha256=<hex>\`) and
\`X-Webhook-Timestamp\`. The scheme mirrors Stripe's: HMAC-SHA256 with the
subscription secret over the timestamp, a literal \`.\`, and the raw request
body. Verify against raw bytes - re-serializing the JSON changes key order and
whitespace and will not match. Reject a timestamp too old to be current, so a
captured signature cannot be replayed.

## ${webhooks.faqHeading}

${webhooks.faq.map((f) => `### ${f.q}\n${f.a}`).join("\n\n")}

## More

- Configuration reference, GCP setup and runner modes: ${site.docsUrl}/gmail-webhooks
- Get connected: ${o}/connect.md
- Full description for LLMs: ${o}/llms-full.txt
`;
}
