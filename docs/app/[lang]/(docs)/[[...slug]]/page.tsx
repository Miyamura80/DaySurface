import { getPageImage, source } from "@/lib/source";
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

export default async function Page({
  params,
}: {
  params: Promise<{ lang: string; slug?: string[] }>;
}) {
  const { lang, slug } = await params;
  const page = source.getPage(slug, lang);
  if (!page) notFound();

  const MDX = page.data.body;
  const gitConfig = {
    user: "Miyamura80",
    repo: "DaySurface",
    branch: "main",
  };

  return (
    <DocsPage toc={page.data.toc} full={page.data.full}>
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

  // Untranslated pages fall back to English content, so every locale resolves
  // to a real URL. Without hreflang those reads as four duplicates of the same
  // page; with it, search engines treat them as one page in four languages and
  // consolidate the ranking signals instead of splitting them.
  // Absolute throughout: metadata is not basePath-aware, so a relative string
  // here would resolve against metadataBase and drop the `/docs` prefix.
  const languages: Record<string, string> = {};
  for (const locale of i18n.languages) {
    const localized = source.getPage(slug, locale);
    if (localized) languages[locale] = absoluteUrl(localized.url);
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
      images: getPageImage(page).url,
    },
    twitter: {
      card: "summary_large_image",
      title: page.data.title,
      description: page.data.description,
      images: getPageImage(page).url,
    },
  };
}
