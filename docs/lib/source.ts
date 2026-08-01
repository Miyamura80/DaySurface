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

/**
 * True when `page` is genuinely written in `locale`, rather than the English
 * fallback fumadocs returns when no translation exists.
 *
 * `page.locale` cannot answer this - it always echoes the *requested* locale,
 * so `getPage(["deployment"], "zh")` reports `locale: "zh"` while serving the
 * English file. `page.path` is the discriminator: a real translation resolves
 * to `index.zh.mdx`, a fallback to `deployment.mdx`.
 *
 * This matters because declaring `hreflang="zh"` for a page serving English is
 * a misdeclaration - only `index` is translated today, so without this check 23
 * of 26 pages would advertise three languages they do not have.
 */
export function isTranslated(
  page: ReturnType<typeof source.getPage> & {},
  locale: string,
): boolean {
  if (locale === i18n.defaultLanguage) return true;
  return page.path.endsWith(`.${locale}.mdx`);
}

export function getPageImage(page: ReturnType<typeof source.getPage> & {}) {
  // Built from `locale` + `slugs` rather than by parsing `page.url`. Under
  // `hideLocale: "default-locale"` the English URL carries no locale segment, so
  // deriving the route from the URL would emit `/og/docs/...` and 404 - the OG
  // route is `/og/[lang]/docs/[...slug]`, where `lang` is always explicit.
  const locale = page.locale ?? i18n.defaultLanguage;
  return {
    url: `/og/${locale}/docs/${[...page.slugs, "og.png"].join("/")}`,
    segments: [...page.slugs, "og.png"],
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
  const withoutFrontmatter = raw
    // Strip the leading YAML frontmatter (title/description are re-added below).
    .replace(/^---\n[\s\S]*?\n---\n?/, "");

  // Rewrite prose only. None of the rules in `stripMdxSyntax` mean anything
  // inside a fence, and applying them there produced code that cannot run: the
  // `import`/`export` rule was deleting `import hashlib` from the webhook
  // verification snippet and `export FOO=...` from shell examples.
  const body = splitFences(withoutFrontmatter)
    .map((segment) => (segment.code ? segment.text : stripMdxSyntax(segment.text)))
    .join("\n");

  return body.trim();
}

/** A run of consecutive lines, tagged by whether they sit inside a code fence. */
type Segment = { code: boolean; text: string };

/** Opens a fence: >=3 backticks or tildes, indented at most 3 spaces. */
const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
/** Closes one: the same run, at least as long, alone on its line. */
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;

/**
 * Partition `body` into alternating prose and code segments.
 *
 * Scans line by line rather than pairing delimiters positionally. Splitting on
 * a `/(^```[\s\S]*?^```)/` regex and trusting the odd indices to be code
 * assumes every delimiter in the document alternates open/close, which one
 * stray ``` inside a code block - a page documenting fences - inverts for the
 * whole remainder of the file, silently swapping which half gets rewritten.
 * Tracking the open delimiter also gets the cases that shape cannot see:
 * indented fences (`docs/content/docs/cli/index.mdx` has one inside a bullet),
 * `~~~` fences, and fences longer than three characters.
 *
 * Reassembly is exact - the segments partition the lines in order, so joining
 * them with the newline that separated them reproduces the input.
 */
function splitFences(body: string): Segment[] {
  const segments: Segment[] = [];
  let buffer: string[] = [];
  /** The delimiter that opened the current fence, or null when in prose. */
  let openedBy: string | null = null;

  const flush = (code: boolean) => {
    if (buffer.length > 0) segments.push({ code, text: buffer.join("\n") });
    buffer = [];
  };

  for (const line of body.split("\n")) {
    if (openedBy === null) {
      const opening = line.match(FENCE_OPEN)?.[1];
      if (opening) {
        flush(false);
        openedBy = opening;
      }
      buffer.push(line);
      continue;
    }

    buffer.push(line);
    const closing = line.match(FENCE_CLOSE)?.[1];
    if (closing && closing[0] === openedBy[0] && closing.length >= openedBy.length) {
      flush(true);
      openedBy = null;
    }
  }

  // An unterminated fence still holds code. Treating the tail as prose would
  // strip exactly the lines the fence was protecting.
  flush(openedBy !== null);
  return segments;
}

/** The MDX-to-Markdown rewrites, applied to prose only - never to code. */
function stripMdxSyntax(prose: string): string {
  return (
    prose
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
      // Collapse the blank lines left behind by the removals. Prose-only, like
      // the rest: run over the whole document it also reflows code, and the
      // blank line PEP 8 wants before a top-level `def` is exactly the run it
      // eats.
      .replace(/\n{3,}/g, "\n\n")
  );
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
