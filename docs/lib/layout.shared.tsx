import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";
import { i18n } from "@/lib/i18n";
import { SITE_NAME, SITE_URL } from "@/lib/site";
import { docsPath } from "@/lib/urls";

export function baseOptions(locale: string): BaseLayoutProps {
  const docsLabels: Record<string, string> = {
    en: "Documentation",
    zh: "文档",
    es: "Documentación",
    ja: "ドキュメント",
  };

  const homeLabels: Record<string, string> = {
    en: "Product",
    zh: "产品",
    es: "Producto",
    ja: "製品",
  };

  return {
    i18n,
    nav: {
      // One product name across every locale - a brand shouldn't be translated,
      // and search engines treat a consistent name as one entity.
      title: `${SITE_NAME} docs`,
      url: docsPath("", locale),
    },
    links: [
      {
        type: "main",
        text: docsLabels[locale] ?? docsLabels.en,
        url: docsPath("", locale),
      },
      // Docs and marketing site share an origin, so this is a same-domain
      // internal link from all ~27 docs pages back to the homepage.
      {
        type: "main",
        text: homeLabels[locale] ?? homeLabels.en,
        url: SITE_URL,
      },
    ],
  };
}
