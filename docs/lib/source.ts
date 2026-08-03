import { createElement } from "react";
import { docs } from "fumadocs-mdx:collections/server";
import { loader } from "fumadocs-core/source";
import { i18n } from "@/lib/i18n";
import { mdxBodyToMarkdown } from "@/lib/mdx-to-markdown";
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

export async function getLLMText(
  page: ReturnType<typeof source.getPage> & {}
): Promise<string> {
  const raw = await page.data.getText("raw");
  const body = mdxBodyToMarkdown(raw);
  const heading = `# ${page.data.title}`;
  const description = page.data.description ?? "";
  return [heading, description, body].filter(Boolean).join("\n\n");
}
