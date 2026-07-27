import { createElement } from "react";
import { docs } from "fumadocs-mdx:collections/server";
import { loader } from "fumadocs-core/source";
import { i18n } from "@/lib/i18n";
import { ChatGPTIcon, ClaudeIcon } from "@/components/icons";

// Custom SVG icons resolved from a page's `icon:` frontmatter field. Add an
// entry here, then set `icon: <key>` in the page frontmatter to show it in the
// sidebar.
const iconMap = {
  claude: ClaudeIcon,
  chatgpt: ChatGPTIcon,
} as const;

export const source = loader({
  baseUrl: "/docs",
  source: docs.toFumadocsSource(),
  i18n,
  icon(icon) {
    if (icon && icon in iconMap) {
      return createElement(iconMap[icon as keyof typeof iconMap]);
    }
  },
});

export function getPageImage(page: ReturnType<typeof source.getPage> & {}) {
  const allSegments = page.url.split("/").filter(Boolean);
  // Strip locale and "docs" prefix for the slug param (they're separate route params)
  const docSegments = allSegments.filter(
    (s) => s !== page.locale && s !== "docs",
  );
  return {
    url: `/og/${allSegments.join("/")}/og.png`,
    segments: [...docSegments, "og.png"],
  };
}

// Turn the raw MDX body into clean Markdown for llms.txt / llms-full.txt.
//
// We deliberately avoid fumadocs' "processed" text here: its heading handler
// drops the `#` depth markers (a `## Section` becomes a bare `Section [#slug]`
// line) and it leaves JSX imports/components inline, so the resulting file has
// no real Markdown structure for an LLM to follow. Working from "raw" lets us
// preserve `##`/`###` sections, fenced code blocks, and inline links, while
// converting the handful of fumadocs UI components we use into plain Markdown.
function mdxBodyToMarkdown(raw: string): string {
  const body = raw
    // Strip the leading YAML frontmatter (title/description are re-added below).
    .replace(/^---\n[\s\S]*?\n---\n?/, "")
    // Drop MDX `import`/`export` statements.
    .replace(/^\s*(?:import|export)\s.+$/gm, "")
    // Drop JSX expression-container props (e.g. `icon={<Rocket />}`). The `>`
    // inside a nested component would otherwise terminate the `[^>]*` tag scans
    // below early, leaving raw JSX in the output. We only emit title/href, so
    // these props are noise for the LLM text anyway.
    .replace(/\s+[A-Za-z_][\w-]*=\{[^}]*\}/g, "")
    // <Card title="X" href="Y" /> -> a Markdown link to the related resource.
    .replace(/<Card\b[^>]*\/?>/g, (tag) => {
      const title = tag.match(/title=["']([^"']*)["']/)?.[1];
      const href = tag.match(/href=["']([^"']*)["']/)?.[1];
      if (title && href) return `- [${title}](${href})`;
      if (title) return `- ${title}`;
      return "";
    })
    // <Tab value="X"> -> a bold label so per-tab content stays attributed.
    .replace(/<Tab\b[^>]*\bvalue=["']([^"']*)["'][^>]*>/g, "\n**$1**\n")
    // Strip the remaining structural component tags, keeping their children.
    .replace(/<\/?(?:Cards|Steps|Step|Tabs|Tab|Callout)\b[^>]*>/g, "")
    // Collapse the blank lines left behind by the removals.
    .replace(/\n{3,}/g, "\n\n");

  return body.trim();
}

export async function getLLMText(
  page: ReturnType<typeof source.getPage> & {}
): Promise<string> {
  const raw = await page.data.getText("raw");
  const body = mdxBodyToMarkdown(raw);
  const heading = `# ${page.data.title}`;
  const description = page.data.description ?? "";
  return [heading, description, body].filter(Boolean).join("\n\n");
}
