/**
 * Markdown twins for the two editorial pages that carry the messaging split:
 *
 *   product.md - how DaySurface works and why it's different (twin of /product)
 *   story.md   - why we built it (twin of /story)
 *
 * Built from the same config the HTML pages render from (`product`, `story`,
 * plus the shared `comparison.pillars` and `features`), so an agent that asks
 * for the markdown gets the same claims without 250KB of markup.
 */
import { site, product, story, comparison, features } from "../config/landing";
import { trimSlash } from "./_shared";

/** /product.md - the how-it-works + why-it's-better document. */
export function buildProductMd(origin: string): string {
  const o = trimSlash(origin);

  const steps = product.steps
    .map((s, i) => `${i + 1}. **${s.title}** - ${s.body}`)
    .join("\n");

  const capabilities = features.items
    .map((f) => `### ${f.title}\n${f.body}`)
    .join("\n\n");

  const pillars = comparison.pillars
    .map((p) => `### ${p.title}\n${p.body}`)
    .join("\n\n");

  return `# ${product.metaTitle}

${product.subhead}

## ${product.howHeading}

${product.howSubhead}

${steps}

## ${product.capabilitiesHeading}

${product.capabilitiesSubhead}

${capabilities}

## ${product.whyHeading}

${product.whySubhead}

${pillars}

See the full capability matrix against other Gmail MCP servers at ${o}/compare.

## Get started

Add ${site.name} to your MCP client: ${o}/connect. Why we built it: ${o}/story.
`;
}

/** /story.md - the founding narrative (twin of /story). */
export function buildStoryMd(origin: string): string {
  const o = trimSlash(origin);

  const sections = story.sections
    .map((s) => `## ${s.heading}\n\n${s.paragraphs.join("\n\n")}`)
    .join("\n\n");

  return `# ${story.heading}

${story.lede}

${sections}

${story.signoff}

---

See how it works: ${o}/product. Add ${site.name} to your client: ${o}/connect.
`;
}
