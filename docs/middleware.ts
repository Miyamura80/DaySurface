import { createI18nMiddleware } from "fumadocs-core/i18n/middleware";
import { i18n } from "@/lib/i18n";

export default createI18nMiddleware(i18n);

export const config = {
  // Exclude machine-facing endpoints from the i18n locale rewrite. Without
  // this, `/docs/sitemap.xml` is rewritten to `/en/docs/sitemap.xml`, which has
  // no route and 404s - the same failure `/llms-full.txt` was already working
  // around. Anything not localized, or carrying its own `lang` segment, belongs
  // in this list, and its route must live outside `[lang]` to match.
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|icon-light.png|icon-dark.png|og/|llms-full.txt|llms.txt|llms.mdx/|docs/sitemap.xml).*)",
  ],
};
