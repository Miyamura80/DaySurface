/**
 * Comparison and API surfaces: compare.md, vs/<id>.md and api.md.
 *
 * All three exist because their HTML counterparts are unreadable to a fetcher -
 * the comparison matrix is a grid of SVG marks, and /api renders itself in
 * client-side JavaScript.
 */
import { site, comparison } from "../config/landing";
import { trimSlash } from "./_shared";

/** Render a matrix cell as text an answer engine can quote without a legend. */
function cellText(cell: { state: string; note?: string } | undefined): string {
  if (!cell) return "not documented";
  const base =
    cell.state === "yes" ? "yes" : cell.state === "partial" ? "partial" : "no";
  return cell.note ? `${base} (${cell.note})` : base;
}

/**
 * compare.md - the /compare hub as markdown, capability matrix included.
 *
 * The HTML version carries the matrix as a table of SVG marks (check / cross /
 * tilde), which survives neither truncation nor a markdown converter: an answer
 * engine reading it sees a grid of empty cells. Every cell is spelled out in
 * words here instead.
 */
export function buildCompareMd(origin: string): string {
  const o = trimSlash(origin);
  const competitorNames = comparison.competitors.map((c) => c.name);
  const header = `| Capability | ${site.name} | ${competitorNames.join(" | ")} |`;
  const divider = `| --- | --- | ${competitorNames.map(() => "---").join(" | ")} |`;
  const rows = comparison.matrix
    .map((r) => {
      const cells = comparison.competitors.map((c) => cellText(r.cells[c.id]));
      const capability = r.detail ? `${r.capability} - ${r.detail}` : r.capability;
      return `| ${capability} | ${cellText(r.us)} | ${cells.join(" | ")} |`;
    })
    .join("\n");

  return `# ${comparison.heading}

> ${comparison.subhead}

Accurate as of ${comparison.asOf}. ${comparison.disclaimer}

## What makes ${site.name} different

${comparison.pillars.map((p) => `### ${p.title}\n${p.body}`).join("\n\n")}

## Capability matrix

${header}
${divider}
${rows}

## Head to head

${comparison.competitors
  .map(
    (c) =>
      `### ${site.name} vs ${c.name}\n` +
      `${c.name} (${c.category}): ${c.blurb} - ${c.url}\n\n` +
      `${c.headline} ${c.summary}\n\n` +
      `- Choose ${site.name} if: ${c.pickUs}\n` +
      `- Choose ${c.name} if: ${c.pickThem}\n` +
      `- Full page: ${o}/vs/${c.id} (markdown: ${o}/vs/${c.id}.md)`,
  )
  .join("\n\n")}

## More

- Get connected: ${o}/connect.md
- Full description for LLMs: ${o}/llms-full.txt
- Pricing: ${o}/pricing.md
`;
}

/** vs/<id>.md - one competitor, in full. */
export function buildVsMd(origin: string, id: string): string {
  const o = trimSlash(origin);
  const c = comparison.competitors.find((x) => x.id === id);
  if (!c) return `# Not found\n\nNo comparison page for \`${id}\`. See ${o}/compare.md.\n`;

  // Carry each row's `detail` (why the capability matters), same as the hub.
  // Without it /vs/<id>.md is not self-contained: an answer engine quoting a
  // bare capability label loses the qualifier the config exists to supply.
  const rows = comparison.matrix
    .map((r) => {
      const capability = r.detail ? `${r.capability} - ${r.detail}` : r.capability;
      return `| ${capability} | ${cellText(r.us)} | ${cellText(r.cells[c.id])} |`;
    })
    .join("\n");

  return `# ${site.name} vs ${c.name}

> ${c.headline} ${c.summary}

${c.name} (${c.category}): ${c.blurb}
- ${c.name}: ${c.url}
- ${site.name}: ${site.url}

Accurate as of ${comparison.asOf}. ${comparison.disclaimer}

## Which to choose

- Choose ${site.name} if: ${c.pickUs}
- Choose ${c.name} if: ${c.pickThem}

## Side by side

| Capability | ${site.name} | ${c.name} |
| --- | --- | --- |
${rows}

## What makes ${site.name} different

${comparison.pillars.map((p) => `### ${p.title}\n${p.body}`).join("\n\n")}

## More

- All comparisons: ${o}/compare.md
- Get connected: ${o}/connect.md
- Full description for LLMs: ${o}/llms-full.txt
`;
}

/**
 * api.md - the /api page as markdown.
 *
 * The HTML page is a Scalar renderer that draws the whole reference in client
 * JS, so a fetcher gets an empty shell. The useful answer for an agent is
 * short: the machine-readable spec is one fetch away, and the same operations
 * exist as MCP tools.
 */
export function buildApiMd(origin: string): string {
  const o = trimSlash(origin);
  return `# ${site.name} HTTP API

> Every ${site.name} tool is reachable over a plain HTTP API as well as over
> MCP and the CLI. One service registry backs all three, so behaviour is
> identical whichever you call.

- OpenAPI specification (machine-readable): ${o}/openapi.json
- Base URL: ${site.apiUrl}
- Rendered reference (browser): ${o}/api

The rendered reference draws itself in client-side JavaScript, so fetch
\`${o}/openapi.json\` rather than scraping that page.

## Authentication

Send an \`X-API-KEY\` header, or an OAuth 2.1 bearer token audience-bound to the
MCP endpoint. Full recipe: ${o}/auth.md

## Prefer MCP for agents

If you are an agent, the MCP endpoint ${site.mcpUrl} gives you the same
operations as typed, self-describing tools - discover them with \`tools/list\`
instead of reading the spec. Setup: ${o}/connect.md

## More

- Full description for LLMs: ${o}/llms-full.txt
- Human docs: ${site.docsUrl}
`;
}
