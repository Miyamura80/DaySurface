import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";
import { i18n } from "@/lib/i18n";

// Product identity, mirrored from the landing page's single source of truth
// (`landing-page/src/config/landing/site.ts`). Same product, so the name,
// links, and CTA read the same on both sites - keep these in step with it.
const SITE_URL = "https://daysurface.com";
const GITHUB_URL = "https://github.com/Miyamura80/DaySurface";

export function baseOptions(locale: string): BaseLayoutProps {
  const labels: Record<string, Record<string, string>> = {
    docs: {
      en: "Documentation",
      zh: "文档",
      es: "Documentación",
      ja: "ドキュメント",
    },
    compare: {
      en: "Compare",
      zh: "对比",
      es: "Comparar",
      ja: "比較",
    },
    pricing: {
      en: "Pricing",
      zh: "价格",
      es: "Precios",
      ja: "料金",
    },
    cta: {
      en: "Get started",
      zh: "开始使用",
      es: "Empezar",
      ja: "はじめる",
    },
  };

  const t = (key: string) => labels[key][locale] ?? labels[key].en;

  return {
    i18n,
    // The brand name is not translated - it reads the same on the landing page
    // in every locale.
    nav: {
      title: "DaySurface",
      url: `/${locale}`,
    },
    githubUrl: GITHUB_URL,
    // Mirrors the landing-page header (Compare / Pricing / GitHub / Get
    // started), with "Docs" swapped for the section link since we are already
    // on the docs site.
    links: [
      {
        type: "main",
        text: t("docs"),
        url: `/${locale}/docs`,
      },
      {
        type: "main",
        text: t("compare"),
        url: `${SITE_URL}/compare`,
        external: true,
      },
      {
        type: "main",
        text: t("pricing"),
        url: `${SITE_URL}/#pricing`,
        external: true,
      },
      {
        type: "button",
        text: t("cta"),
        url: `${SITE_URL}/#connect`,
        external: true,
      },
    ],
  };
}
