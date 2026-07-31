/**
 * Standalone manifests: skills.sh, auth.md (auth-md.com) and pricing.md.
 */
import { site, pricing, pricingAxes, addOns, selfHost } from "../config/landing";
import { trimSlash } from "./_shared";

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
  return `#!/usr/bin/env sh
# ${site.name} - agent skill discovery
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
