import { getPageImage, isTranslated, source } from "@/lib/source";
import {
  DocsBody,
  DocsDescription,
  DocsPage,
  DocsTitle,
} from "fumadocs-ui/layouts/docs/page";
import { notFound } from "next/navigation";
import { getMDXComponents } from "@/mdx-components";
import type { Metadata } from "next";
import { createRelativeLink } from "fumadocs-ui/mdx";
import { LLMCopyButton, ViewOptions } from "@/components/ai/page-actions";
import { i18n } from "@/lib/i18n";
import { SITE_NAME, absoluteUrl } from "@/lib/site";
import { buildDocsJsonLd } from "@/lib/structured-data";
import { findPath } from "fumadocs-core/page-tree";

/**
 * Ancestor trail for a page's breadcrumbs, e.g. `/docs/mcp/setup` yields
 * Documentation -> MCP Server.
 *
 * Walks the page tree rather than the slug segments so each folder is named by
 * its `meta.json` title, exactly as the sidebar names it. Deriving the name
 * from the folder's index page instead would label `/docs/mcp` "Overview" -
 * that page's own title - which is meaningless as a breadcrumb.
 *
 * The folder's URL comes from the slug rather than `Folder.index`, which is
 * only populated when the index page is *implicit*. Every `meta.json` here
 * lists `"index"` in `pages`, so the landing page is an ordinary child and
 * `Folder.index` is undefined - reading it dropped the folder crumb entirely.
 * Nth folder in the tree path therefore pairs with the first N slug segments,
 * and the pairing is confirmed against the loader before a crumb is emitted, so
 * a folder with no landing page is skipped instead of linking to a 404.
 */
function buildBreadcrumbs(
  slug: string[],
  url: string,
  lang: string,
): { name: string; url: string }[] {
  const tree = source.pageTree[lang];
  const rootName = tree?.name;
  const crumbs = [
    {
      name: typeof rootName === "string" ? rootName : "Documentation",
      url: "/docs",
    },
  ];

  const path =
    findPath(
      tree?.children ?? [],
      (node) => node.type === "page" && node.url === url,
    ) ?? [];

  let depth = 0;
  for (const node of path) {
    if (node.type !== "folder") continue;
    const prefix = slug.slice(0, ++depth);
    const index = source.getPage(prefix, lang);
    if (!index || typeof node.name !== "string") continue;
    crumbs.push({ name: node.name, url: index.url });
  }

  return crumbs;
}

export default async function Page({
  params,
}: {
  params: Promise<{ lang: string; slug?: string[] }>;
}) {
  const { lang, slug } = await params;
  const page = source.getPage(slug, lang);
  if (!page) notFound();

  const MDX = page.data.body;
  const jsonLd = buildDocsJsonLd({
    url: page.url,
    title: page.data.title,
    description: page.data.description,
    imageUrl: getPageImage(page).url,
    breadcrumbs: buildBreadcrumbs(slug ?? [], page.url, lang),
  });
  const gitConfig = {
    user: "Miyamura80",
    repo: "DaySurface",
    branch: "main",
  };

  return (
    <DocsPage toc={page.data.toc} full={page.data.full}>
      {/* Rendered in the body rather than via Next metadata: `generateMetadata`
          has no hook for arbitrary JSON-LD, and Google reads it from anywhere in
          the document. */}
      <script
        type="application/ld+json"
        // Serialized from typed page data, never user input.
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      <DocsTitle>{page.data.title}</DocsTitle>
      <DocsDescription className="mb-0">
        {page.data.description}
      </DocsDescription>
      <div className="flex flex-row gap-2 items-center border-b pb-6">
        <LLMCopyButton markdownUrl={`${page.url}.mdx`} />
        <ViewOptions
          markdownUrl={`${page.url}.mdx`}
          githubUrl={`https://github.com/${gitConfig.user}/${gitConfig.repo}/blob/${gitConfig.branch}/docs/content/docs/${page.path}`}
        />
      </div>
      <DocsBody>
        <MDX
          components={getMDXComponents({
            a: createRelativeLink(source, page),
          })}
        />
      </DocsBody>
    </DocsPage>
  );
}

export async function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ lang: string; slug?: string[] }>;
}): Promise<Metadata> {
  const { lang, slug } = await params;
  const page = source.getPage(slug, lang);
  if (!page) notFound();

  // Only advertise a locale that actually has a translation. `getPage` falls
  // back to English rather than returning null, so a naive loop would declare
  // `hreflang="zh"` for a page serving English - a misdeclaration, and on all
  // but the three translated pages. A page with no translations ends up with
  // just its own entry plus x-default, which is the correct signal.
  const languages: Record<string, string> = {};
  for (const locale of i18n.languages) {
    const localized = source.getPage(slug, locale);
    if (localized && isTranslated(localized, locale)) {
      languages[locale] = absoluteUrl(localized.url);
    }
  }

  return {
    title: page.data.title,
    description: page.data.description,
    alternates: {
      canonical: absoluteUrl(page.url),
      languages: {
        ...languages,
        "x-default": absoluteUrl(
          source.getPage(slug, i18n.defaultLanguage)?.url ?? page.url,
        ),
      },
    },
    openGraph: {
      type: "article",
      siteName: `${SITE_NAME} docs`,
      title: page.data.title,
      description: page.data.description,
      url: absoluteUrl(page.url),
      images: absoluteUrl(getPageImage(page).url),
    },
    twitter: {
      card: "summary_large_image",
      title: page.data.title,
      description: page.data.description,
      images: absoluteUrl(getPageImage(page).url),
    },
  };
}
