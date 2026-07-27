import { createI18nMiddleware } from "fumadocs-core/i18n/middleware";
import { i18n } from "@/lib/i18n";

export default createI18nMiddleware(i18n);

export const config = {
  // Exclude machine-facing endpoints from the i18n locale redirect. Without
  // this, `/llms-full.txt` is rewritten to `/en/llms-full.txt`, which has no
  // route and 404s; the LLM-text routes carry their own `lang` instead.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|icon-light.png|icon-dark.png|og/|llms-full.txt|llms.txt|llms.mdx/).*)"],
};
