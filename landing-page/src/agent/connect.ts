/**
 * The connect surfaces: `/connect.md` (and every intent alias) plus the 404
 * body. Both answer the same question - "how do I get started / is there an
 * account" - which is the one an agent asks first and the site could not
 * previously answer at all.
 */
import { site, connectPage, groupedInstallMatrix, type InstallClient } from "../config/landing";
import { trimSlash, indent } from "./_shared";

/**
 * The endpoint facts block, identical everywhere it appears. Indented as a
 * fenced-free code block so it survives markdown-to-text conversion intact.
 */
function factsBlock(): string {
  const width = Math.max(...connectPage.facts.map((f) => f.label.length)) + 2;
  return connectPage.facts
    .map((f) => `    ${(f.label + ":").padEnd(width)}${f.value}`)
    .join("\n");
}

/**
 * One client's entry in the connect matrix.
 *
 * `sharedPrompt` is the setup prompt already printed once at the group level;
 * a client carrying that same text omits its own copy. Four prompt targets
 * share one ~450-char prompt, and repeating it inflated the document by a
 * third - in a file whose whole purpose is to stay under a fetcher's
 * truncation limit.
 */
function clientEntry(c: InstallClient, sharedPrompt?: string | null): string {
  const lines: string[] = [];
  if (c.install_url) {
    lines.push(`- **${c.name}** - ${c.install_url}`);
  } else if (c.setup_kind === "command" && c.setup_prompt) {
    lines.push(`- **${c.name}** - run \`${c.setup_prompt}\``);
  } else {
    lines.push(`- **${c.name}**`);
  }
  if (c.note) lines.push(`  ${c.note}`);
  // Prompt targets are useless without the text itself - an agent reading this
  // cannot go and fetch "the setup prompt from the site".
  if (c.setup_prompt && c.setup_kind !== "command" && c.setup_prompt !== sharedPrompt) {
    lines.push("", indent("```"), indent(c.setup_prompt), indent("```"));
  }
  // Deep links carry their click-path as a FALLBACK; a `dialog-only` link has
  // genuinely not done the work yet, so the steps are the remaining job. Label
  // which, or an agent reports an install that never happened.
  if (c.steps?.length) {
    const label =
      c.effort === "dialog-only"
        ? "  Then, in the dialog the link opens:"
        : "  If the link does not work:";
    lines.push("", label);
    lines.push(...c.steps.map((s, i) => `  ${i + 1}. ${s}`));
  }
  return lines.join("\n");
}

/** The setup prompt every client in a group shares, or null if they differ. */
function sharedPromptOf(clients: InstallClient[]): string | null {
  const first = clients[0]?.setup_prompt;
  if (!first || clients.length < 2) return null;
  return clients.every((c) => c.setup_prompt === first) ? first : null;
}

/**
 * connect.md - the canonical answer to "how do I sign up / get started", and
 * the body served at every intent alias (/signup, /login, ...).
 *
 * Order is deliberate and load-bearing. The premise-refuting sentence comes
 * first, then the endpoint (the whole job for an agent that can install MCP
 * servers itself), and only then the per-client matrix - because the reader
 * that fetched this URL is more often an agent than a human, and we do not
 * know which client it is running in.
 */
export function buildConnectMd(origin: string): string {
  const o = trimSlash(origin);
  const groups = groupedInstallMatrix()
    .map((g) => {
      const shared = sharedPromptOf(g.clients);
      const preamble = shared ? `Paste this into any of the clients below:\n\n\`\`\`\n${shared}\n\`\`\`\n\n` : "";
      const body = g.clients.map((c) => clientEntry(c, shared)).join("\n\n");
      return `## ${g.heading}\n\n${preamble}${body}`;
    })
    .join("\n\n");

  return `# ${connectPage.title}

${connectPage.noAccount}

${factsBlock()}

${connectPage.agentShortcut}

${groups}

## After connecting

Your client completes Google OAuth in the browser and the tools appear
automatically - discover them with \`tools/list\` and invoke them with
\`tools/call\`. Nothing is installed locally and there is no dashboard to visit.

## More

- Full description for LLMs: ${o}/llms-full.txt
- Agent guide: ${o}/agents.md
- Agent auth (auth.md): ${o}/auth.md
- Install matrix (JSON): ${o}/.well-known/mcp/install.json
- Human docs: ${site.docsUrl}
- Source: ${site.githubUrl}
`;
}

/**
 * The body of a 404, as markdown.
 *
 * A 404 that says only "Not found" costs an agent the whole request. This one
 * spends ~500 bytes telling it where everything is, so a wrong guess still
 * advances the task. Note this document exists at all only because the site
 * stopped answering unknown paths with the homepage at a 200 - which is what
 * made route-probing indistinguishable from a client-rendered app.
 */
export function build404Md(origin: string, pathname: string): string {
  const o = trimSlash(origin);
  return `# 404 - no such page

\`${pathname}\` is not a page on ${site.name}.

${site.name} is an MCP server for Gmail. There is no account to create and
nothing to install - see ${o}/connect.

## Every page on this site

- ${o}/ - what ${site.name} is
- ${o}/connect - how to connect it to any MCP client (also /connect.md)
- ${o}/pricing - plans (also /pricing.md)
- ${o}/compare - how it compares to other Gmail MCP servers
- ${o}/api - the HTTP API
- ${o}/docs - full documentation
- ${o}/privacy, ${o}/terms

## Machine-readable

- ${o}/llms.txt, ${o}/llms-full.txt, ${o}/agents.md, ${o}/auth.md
- ${o}/.well-known/mcp/install.json - install matrix, every client
- ${o}/.well-known/mcp/server-card.json - pre-connect server card
- ${o}/sitemap.xml
`;
}
