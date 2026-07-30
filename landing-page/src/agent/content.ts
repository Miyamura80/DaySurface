/**
 * AI / agent discoverability content, generated from `config/landing.ts`.
 *
 * One source of truth (the landing config) drives every machine-readable
 * surface: llms.txt, llms-full.txt, agents.md and the in-page agent view.
 * Rebranding the site (editing landing.ts) keeps all of these in sync.
 */
import { site, hero, features, faq, compatibility, connect, comparison, pricing, pricingAxes, addOns, selfHost, agentGuide, purpose, purposeText } from "../config/landing";

/** Strip a trailing slash so we can safely append paths. */
function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/**
 * "What <product> does" - the plain-language purpose + Gmail-permission
 * statement, identical to the block rendered under the hero (Purpose.astro).
 *
 * It leads every agent surface because it is the one thing a reader with no
 * context needs first: what the product does, what it asks of the user's
 * mailbox, and what happens to the data. The transport/architecture story
 * follows it, not the other way round.
 */
function purposeSection(origin: string): string {
  return `## ${purpose.heading}\n\n${purposeText(origin)}`;
}

/** Full "When to use" section shared by the long-form surfaces (llms-full.txt, agents.md). */
function whenToUseSection(): string {
  return `## When to use

${agentGuide.summary}

Use ${site.name} when:
${agentGuide.whenToUse.map((s) => `- ${s}`).join("\n")}

Do not use ${site.name} when:
${agentGuide.whenNotToUse.map((s) => `- ${s}`).join("\n")}`;
}

/** Concise llms.txt index (see https://llmstxt.org). */
export function buildLlmsTxt(origin: string): string {
  const o = trimSlash(origin);
  return `# ${site.name}

> ${site.description}

${hero.subhead}

${purposeSection(o)}

## When to use

${agentGuide.summary}
${agentGuide.whenToUse.map((s) => `- ${s}`).join("\n")}

## Connect over MCP
- [MCP endpoint](${site.mcpUrl}): Streamable-HTTP MCP server URL to add to your client. Server name: \`${site.serverName}\`.

## Documentation for LLMs
- [llms-full.txt](${o}/llms-full.txt): Full, expanded description of what ${site.name} is and how to use every transport.
- [agents.md](${o}/agents.md): Agent-oriented capability and skills summary.
- [How it compares](${o}/compare): ${site.name} vs other Gmail MCP servers (GongRzhe, Composio, Zapier/Pipedream, Google Workspace MCP).

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
  // layered. The CLI/HTTP story survives under "How it works" and on /api.
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

${purposeSection(o)}

## How it works

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
- auth.md (agent auth manifest): ${o}/auth.md
- pricing.md (machine-readable pricing): ${o}/pricing.md
- Agent skills (JSON): ${o}/.well-known/agent-skills/index.json
- Agent skills (shell pointer): ${o}/skills.sh
- MCP discovery (JSON): ${o}/.well-known/mcp.json
- Sitemap: ${o}/sitemap.xml
- Schema map: ${o}/schemamap.xml
`;
}

/**
 * skills.sh - shell-friendly agent skill discovery pointer.
 *
 * Not a formal standard (the canonical index is the Agent Skills Discovery
 * JSON below); this exists so scanners that probe /skills.sh get a real 200
 * with the discovery URLs instead of an SPA fallback. It is read-only and
 * makes no changes when run.
 */
export function buildSkillsSh(origin: string): string {
  const o = trimSlash(origin);
  // Wrap the purpose prose into shell comments so a reader who curls this file
  // learns what the product is before being pointed at the skill index.
  const purposeComment = purposeText(o)
    .split("\n")
    .map((line) => (line ? `# ${line}` : "#"))
    .join("\n");
  return `#!/usr/bin/env sh
# ${site.name} - agent skill discovery
#
${purposeComment}
#
# ${site.name} is an MCP server. The machine-readable skill index lives at the
# path below (Agent Skills Discovery, schema 0.2.0). This script only prints
# pointers; it makes no changes to your system.

SKILLS_INDEX="${o}/.well-known/agent-skills/index.json"
MCP_ENDPOINT="${site.mcpUrl}"

echo "Skill index:  $SKILLS_INDEX"
echo "MCP endpoint: $MCP_ENDPOINT (server name: ${site.serverName})"
echo
echo "Fetch the skill index:"
echo "  curl -fsSL $SKILLS_INDEX"
`;
}

/** agents.md - agent/skills oriented capability summary. */
export function buildAgentsMd(origin: string): string {
  const o = trimSlash(origin);
  return `# ${site.name} - agent guide

${site.description}

This site documents an MCP server. Agents should connect over MCP to use its
tools rather than scraping this page.

${purposeSection(o)}

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

/**
 * auth.md - agent authentication manifest, served at the canonical `/auth.md`
 * path of the WorkOS auth.md convention (https://auth-md.com).
 *
 * Foot-in-the-door: this documents the authentication that is actually live -
 * OAuth 2.1 (MCP authorization spec, resource server + RFC 9728 discovery) and
 * API keys - written as the auth.md procedural recipe. The full
 * agent-registration extension (agent-attested ID-JAG identity assertions,
 * claim ceremonies, an `agent_auth` discovery block) is explicitly marked
 * not-yet-implemented, so we advertise readiness without pointing agents at
 * endpoints that do not exist.
 */
export function buildAuthMd(origin: string): string {
  const o = trimSlash(origin);
  const mcpOrigin = trimSlash(new URL(site.mcpUrl).origin);
  return `# ${site.name} - auth.md

> Agent authentication manifest for ${site.name}, following the auth.md
> convention (https://auth-md.com). It tells an autonomous agent how to
> authenticate to this service on a user's behalf.

- Service: ${site.name}
- MCP endpoint (streamable HTTP): ${site.mcpUrl}
- OAuth 2.0 Protected Resource Metadata (RFC 9728): ${mcpOrigin}/.well-known/oauth-protected-resource/mcp
- Website: ${site.url}

${purposeSection(o)}

## What is supported today

${site.name} is an OAuth 2.1 **resource server** (MCP authorization spec,
2025-11-25). Two credentials are accepted across every transport:

1. **OAuth 2.1 bearer token** - interactive, user-in-the-loop consent. The
   authorization server handles client registration, PKCE, and the consent
   screen, then issues tokens audience-bound to the MCP endpoint above.
2. **API key** - a long-lived \`X-API-KEY\` header for machine / first-party
   clients, with granular scopes.

## Procedural recipe

1. **Discover.** Fetch the Protected Resource Metadata above. It names the
   canonical resource URI and the \`authorization_servers\` to use. An
   unauthenticated request to the MCP endpoint also returns
   \`WWW-Authenticate: Bearer ... resource_metadata="..."\`, which bootstraps
   the flow.
2. **Authenticate.** Complete the OAuth 2.1 authorization-code + PKCE flow with
   the advertised authorization server to obtain a bearer token, or present a
   pre-issued API key.
3. **Use.** Call the MCP endpoint with \`Authorization: Bearer <token>\` (or
   \`X-API-KEY: <key>\`). Discover tools via \`tools/list\` and invoke them via
   \`tools/call\`.
4. **Revoke.** Bearer tokens are revoked at the authorization server; API keys
   are revoked from the dashboard or the \`/api/v1/auth/api-keys\` endpoint.

## Agent registration (auth.md protocol) - roadmap

The full auth.md agent-registration extension - agent-attested identity via
ID-JAG assertions (draft-ietf-oauth-identity-assertion-authz-grant) plus
user-claimed / anonymous claim ceremonies, advertised through an \`agent_auth\`
block in the authorization-server metadata - is **not yet implemented**. Today
agents authenticate through the standard interactive OAuth 2.1 consent flow
above. This manifest will be extended to the full protocol once first-class
support lands in the upstream authorization server.

## More

- MCP discovery (JSON): ${o}/.well-known/mcp.json
- Agent guide: ${o}/agents.md
- Full description for LLMs: ${o}/llms-full.txt
- Source: ${site.githubUrl}
`;
}

/**
 * pricing.md - machine-readable pricing manifest served at the canonical
 * `/pricing.md` path.
 *
 * AI agents comparing products and making purchase recommendations need
 * pricing as plain markdown, not scraped from an HTML pricing page. This is
 * generated from the `pricing` block in landing.ts regardless of
 * `pricing.enabled` (that flag only controls the on-page section), so the
 * machine-readable manifest exists even when the human pricing section is
 * deferred to a separate page.
 */
export function buildPricingMd(origin: string): string {
  const o = trimSlash(origin);
  const tierBlock = pricing.tiers
    .map((t) => {
      // Glue slash cadences ("$20/mo") but space-separate word cadences ("$0 forever").
      const cadence = t.cadence ?? "";
      const price = cadence && !cadence.startsWith("/") ? `${t.price} ${cadence}` : `${t.price}${cadence}`;
      const featureLines = t.features.map((f) => `- ${f}`).join("\n");
      return (
        `## ${t.name}\n\n` +
        `- Price: ${price}\n` +
        (t.note ? `- Terms: ${t.note}\n` : "") +
        `- Summary: ${t.description}\n\n` +
        `Includes:\n${featureLines}`
      );
    })
    .join("\n\n");

  return `# ${site.name} - pricing

> Machine-readable pricing for ${site.name}, for AI agents comparing products
> and making purchase recommendations. ${pricing.subhead}

- Service: ${site.name}
- Website: ${site.url}
- Source code (open source, self-hostable): ${site.githubUrl}

${tierBlock}

## How the tiers are drawn

${pricing.principle}

${pricingAxes.items
  .map((a) => `- **${a.name}** (${a.tier}) - free: ${a.free} Paid: ${a.paid}`)
  .join("\n")}

## Add-ons

Attach to any paid tier, self-serve. Not a separate contract.

${addOns.items
  .map(
    (a) =>
      `### ${a.name} add-on\n\n` +
      `- Price: ${a.price}${a.cadence}\n` +
      `- Available on: ${a.availableOn}\n` +
      `- Summary: ${a.description}\n\n` +
      `Includes:\n${a.features.map((f) => `- ${f}`).join("\n")}`,
  )
  .join("\n\n")}

## Self-hosted (open source)

- Price: **$0**, no licence fee, no seat count.
- Summary: ${selfHost.body}

Includes:
${selfHost.points.map((pt) => `- ${pt}`).join("\n")}
- Source: ${site.githubUrl}

## Notes

- Licensing model: ${site.name} is open source under the **MIT license** - free
  to use, modify, and self-host. Every feature listed here is available when
  self-hosting; entitlement checks are disabled by default in the source.
- Setup cost: **none.** Self-hosting has no license or setup fee; the hosted
  tiers are paste-a-URL onboarding with no setup charge.
- The free tier is not a trial and does not expire. No card is required.
- **Data retention applies only to ${site.name}-generated memory** (triage
  verdicts, summaries, document sessions) - never to the user's mail, which
  stays in Gmail and is searchable in full on every tier, including free.
- On downgrade or failed payment, read access is retained and stored memory is
  frozen rather than deleted.

Prices are denominated as shown above. For current, authoritative pricing
always check ${site.url}.

## More

- Full description for LLMs: ${o}/llms-full.txt
- Agent guide: ${o}/agents.md
- Agent auth (auth.md): ${o}/auth.md
`;
}
