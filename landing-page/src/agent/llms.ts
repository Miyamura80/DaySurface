/**
 * The site-wide agent indexes: llms.txt, llms-full.txt and agents.md.
 */
import { site, hero, features, faq, compatibility, connect, comparison, connectPage, agentGuide } from "../config/landing";
import { trimSlash, whenToUseSection } from "./_shared";

/** Concise llms.txt index (see https://llmstxt.org). */
export function buildLlmsTxt(origin: string): string {
  const o = trimSlash(origin);
  return `# ${site.name}

> ${site.description}

${hero.subhead}

## Getting started

${connectPage.noAccount}

## When to use

${agentGuide.summary}
${agentGuide.whenToUse.map((s) => `- ${s}`).join("\n")}

## Connect over MCP
- [MCP endpoint](${site.mcpUrl}): Streamable-HTTP MCP server URL to add to your client. Server name: \`${site.serverName}\`.
- [connect.md](${o}/connect.md): Every supported client with its one-click install link, setup command, or paste-in prompt. Also served at /signup, /login, /get-started and other aliases.
- [install.json](${o}/.well-known/mcp/install.json): The same install matrix as JSON.

## Documentation for LLMs
- [llms-full.txt](${o}/llms-full.txt): Full, expanded description of what ${site.name} is and how to use every transport.
- [agents.md](${o}/agents.md): Agent-oriented capability and skills summary.
- [compare.md](${o}/compare.md): ${site.name} vs other Gmail MCP servers (GongRzhe, Composio, Zapier/Pipedream, Google Workspace MCP), capability matrix spelled out in words.
- [api.md](${o}/api.md): The HTTP API and where to fetch its OpenAPI spec.

Every HTML page with a markdown twin also answers \`Accept: text/markdown\` on its
own URL, and advertises the twin as \`<link rel="alternate" type="text/markdown">\`.

## Pricing & licensing
- [pricing.md](${o}/pricing.md): Machine-readable pricing & tiers. ${site.name} is open source (MIT license) and free to self-host with no setup cost; paid tiers cover autonomy (background follow-up and watches) and throughput, with governance sold as an add-on.

## Resources
- [Documentation](${site.docsUrl})
- [Source code](${site.githubUrl})

## Optional
- [FAQ](${o}/#faq): Common questions about clients, transports, auth and self-hosting.
`;
}

/** Long-form llms-full.txt: everything an agent needs in one fetch. */
export function buildLlmsFullTxt(origin: string): string {
  const o = trimSlash(origin);
  const featureBlock = features.items
    .map((f) => `### ${f.title}\n${f.body}`)
    .join("\n\n");
  // Per-client connect instructions. Replaces the old three-transports block:
  // an agent needs to know how its host gets wired up, not how the codebase is
  // layered. The CLI/HTTP story survives under "What it is" and on /api.
  const numbered = (steps?: string[]) =>
    (steps ?? []).map((s, i) => `${i + 1}. ${s}`).join("\n");
  const connectBlock = connect.targets
    .map((t) => {
      let how: string;
      if (t.method === "deeplink" && t.prefills !== false) {
        how = "One-click install link on the site.";
      } else if (t.method === "deeplink") {
        // A deep link that prefills nothing gets the click-path too, but the
        // steps have to be labelled as the FALLBACK: the link already performs
        // the first two, so an agent reading them as remaining work would try
        // to navigate to a dialog that is open in front of it.
        how =
          `Install link on the site opens the setup dialog; the fields come up empty, ` +
          `so paste the server URL there and confirm. If the link is blocked or ` +
          `unavailable, the full path from scratch is:\n${numbered(t.steps)}`;
      } else if (t.method === "prompt" && t.setupPrompt) {
        // Inline the actual text - an agent reading this cannot go and fetch
        // "the setup prompt from the site".
        how = `${t.setupKind === "command" ? "Run this in a terminal" : "Paste this into the agent"}:\n\n${t.setupPrompt}`;
      } else {
        how = numbered(t.steps);
      }
      return `### ${t.name}\n${how}${t.note ? `\n${t.note}` : ""}`;
    })
    .join("\n\n");
  const faqBlock = faq.items.map((i) => `### ${i.q}\n${i.a}`).join("\n\n");
  const clients = compatibility.hosts.map((h) => h.name).join(", ");

  const pillarsBlock = comparison.pillars
    .map((p) => `- **${p.title}**: ${p.body}`)
    .join("\n");
  const competitorBlock = comparison.competitors
    .map(
      (c) =>
        `### ${site.name} vs ${c.name}\n` +
        `${c.headline} ${c.summary}\n\n` +
        `- Choose ${site.name} if: ${c.pickUs}\n` +
        `- Choose ${c.name} if: ${c.pickThem}\n` +
        `- Full comparison: ${o}/vs/${c.id}`,
    )
    .join("\n\n");

  return `# ${site.name} - ${site.tagline}

> ${site.description}

${hero.headline} ${hero.subhead}

- Website: ${site.url}
- MCP endpoint (streamable HTTP): ${site.mcpUrl}
- MCP server name: ${site.serverName}
- Documentation: ${site.docsUrl}
- Source code: ${site.githubUrl}

## Getting started

${connectPage.noAccount}

Every client's install link, setup command or paste-in prompt is at
${o}/connect (markdown: ${o}/connect.md, JSON: ${o}/.well-known/mcp/install.json).

## What it is

${site.name} is a Model Context Protocol (MCP) server. It exposes a single
shared service registry over three interfaces - a CLI, an MCP server
(streamable HTTP), and a plain HTTP API - so the same typed tools behave
identically no matter how they are called. Any agent that speaks MCP can
discover and call its tools.

${whenToUseSection()}

## Connecting

Add the streamable-HTTP endpoint ${site.mcpUrl} to any MCP client. Nothing to
install locally. Server name: \`${site.serverName}\`.

${connectBlock}

## Features

${featureBlock}

## How it compares

${comparison.subhead}

What makes ${site.name} different (as of ${comparison.asOf}):
${pillarsBlock}

${competitorBlock}

See the full comparison and capability matrix at ${o}/compare.

## Compatible clients

Works with every MCP client, including: ${clients}.

## How to connect

1. Copy the MCP server URL: ${site.mcpUrl}
2. Add it to your client (server name \`${site.serverName}\`):
${connect.targets
  .map((t) => {
    // Every method needs its own branch. Falling through to `steps` left the
    // five prompt targets emitting a bare "   - Cline: " with no instructions.
    if (t.method === "deeplink" && t.prefills !== false)
      return `   - ${t.name}: one-click install (deep link supported).`;
    // ChatGPT's deep link only opens the dialog, so it falls through to the
    // steps below rather than claiming an install the visitor hasn't done.
    if (t.method === "prompt" && t.setupPrompt) {
      // A one-line command is worth inlining; the multi-line prompt is not -
      // it would repeat ~450 chars four times in what is meant to be a quick
      // reference. The full text is under "Connecting" above, same document.
      return t.setupKind === "command"
        ? `   - ${t.name}: run \`${t.setupPrompt}\``
        : `   - ${t.name}: paste the setup prompt shown under "Connecting" above.`;
    }
    return `   - ${t.name}: ${(t.steps ?? []).join(" → ")}`;
  })
  .join("\n")}
3. Your agent discovers the tools automatically and calls them with typed inputs.

## FAQ

${faqBlock}

## Machine-readable resources

- llms.txt: ${o}/llms.txt
- llms-full.txt: ${o}/llms-full.txt
- agents.md: ${o}/agents.md
- connect.md (how to connect, every client): ${o}/connect.md
- Install matrix (JSON): ${o}/.well-known/mcp/install.json
- auth.md (agent auth manifest): ${o}/auth.md
- pricing.md (machine-readable pricing): ${o}/pricing.md
- Agent skills (JSON): ${o}/.well-known/agent-skills/index.json
- Agent skills (shell pointer): ${o}/skills.sh
- MCP discovery (JSON): ${o}/.well-known/mcp.json
- Sitemap: ${o}/sitemap.xml
- Schema map: ${o}/schemamap.xml
`;
}

/** agents.md - agent/skills oriented capability summary. */
export function buildAgentsMd(origin: string): string {
  const o = trimSlash(origin);
  return `# ${site.name} - agent guide

${site.description}

This site documents an MCP server. Agents should connect over MCP to use its
tools rather than scraping this page.

## Getting started

${connectPage.noAccount}

Per-client install links: ${o}/connect.md (JSON: ${o}/.well-known/mcp/install.json).

${whenToUseSection()}

## MCP server

- Endpoint (streamable HTTP): \`${site.mcpUrl}\`
- Server name: \`${site.serverName}\`
- Discovery: ${o}/.well-known/mcp.json

## How to use

1. Add the MCP endpoint above to your client.
2. List tools via the MCP \`tools/list\` method.
3. Call tools via \`tools/call\` with typed JSON arguments.

The same tools are also reachable over a CLI and a plain HTTP API; behaviour
is identical across all three transports.

## How it compares

${site.name} is a dedicated Gmail MCP, not a thin API wrapper or a generic
multi-app gateway. Its differentiators: interactive in-chat UI (MCP Apps),
one codebase exposed over CLI + MCP + HTTP, and an open-source, self-hostable
server. Head-to-head comparisons:
${comparison.competitors
  .map((c) => `- vs ${c.name}: ${o}/vs/${c.id}`)
  .join("\n")}

Full matrix: ${o}/compare

## More

- Agent auth (auth.md): ${o}/auth.md
- Pricing (pricing.md): ${o}/pricing.md
- Full description for LLMs: ${o}/llms-full.txt
- Skills (JSON): ${o}/.well-known/agent-skills/index.json
- Skills (shell pointer): ${o}/skills.sh
- Human docs: ${site.docsUrl}
- Source: ${site.githubUrl}
`;
}
