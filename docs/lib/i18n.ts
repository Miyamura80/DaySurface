import { defineI18n } from "fumadocs-core/i18n";

export const i18n = defineI18n({
  defaultLanguage: "en",
  languages: ["en", "zh", "es", "ja"],
  // Serve English at `/docs/...` instead of `/en/docs/...`. The prefixed form
  // 307-redirects to the bare one, so there is a single canonical URL per page
  // and no redirect hop between the marketing site and the docs it links to.
  hideLocale: "default-locale",
});
