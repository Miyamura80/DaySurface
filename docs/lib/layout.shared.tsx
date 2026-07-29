import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";
import { i18n } from "@/lib/i18n";

// Product identity, mirrored from the landing page's single source of truth
// (`landing-page/src/config/landing/site.ts`). Same product, so the name and
// links read the same on both sites - keep these in step with it. They are
// copied rather than imported because the two sites are separate deploys with
// separate bun projects and no shared workspace.
const SITE_URL = "https://daysurface.com";
const GITHUB_URL = "https://github.com/Miyamura80/DaySurface";

type Locale = (typeof i18n.languages)[number];

// Locale -> label, so a new locale is one object with every key checked at
// compile time instead of a missing key silently falling back to English.
const NAV_LABELS = {
  en: { docs: "Documentation", compare: "Compare", cta: "Get started" },
  zh: { docs: "文档", compare: "对比", cta: "开始使用" },
  es: { docs: "Documentación", compare: "Comparar", cta: "Empezar" },
  ja: { docs: "ドキュメント", compare: "比較", cta: "はじめる" },
} as const satisfies Record<Locale, Record<string, string>>;

export function baseOptions(locale: string): BaseLayoutProps {
  const t = NAV_LABELS[locale as Locale] ?? NAV_LABELS.en;

  return {
    i18n,
    // The brand name is not translated - it reads the same on the landing page
    // in every locale.
    nav: {
      title: "DaySurface",
      url: `/${locale}`,
    },
    githubUrl: GITHUB_URL,
    // Carries over the landing header's Compare link, GitHub mark, and connect
    // CTA; "Docs" becomes the section link since we are already on the docs
    // site. Deliberately no Pricing link: the landing page ships `/pricing`
    // conditionally (`pricing.enabled`), so hardlinking that route here would
    // put a dead link on every docs page the moment pricing is turned off. The
    // docs index links pricing from its body instead.
    links: [
      {
        type: "main",
        text: t.docs,
        url: `/${locale}/docs`,
      },
      {
        type: "main",
        text: t.compare,
        url: `${SITE_URL}/compare`,
        external: true,
      },
      {
        type: "button",
        text: t.cta,
        url: `${SITE_URL}/#connect`,
        external: true,
      },
    ],
  };
}
