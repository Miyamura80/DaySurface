import {
  createI18nMiddleware,
  DefaultFormatter,
} from "fumadocs-core/i18n/middleware";
import { i18n } from "@/lib/i18n";

/**
 * Collapse duplicate slashes and drop a trailing one (except on bare "/").
 *
 * `DefaultFormatter` builds paths as `${basePath}/${locale}/${pathname}` and
 * only collapses repeated slashes. At the docs root `pathname` is "/", so it
 * yields `/docs/en/` - and a *rewrite* to a trailing-slash path does not match
 * the `/[lang]` route under the default `trailingSlash: false`, which 404s the
 * docs index while every deeper page works. Normalizing here fixes the root and
 * also removes a redirect hop on `/docs/en` -> `/docs`.
 */
function normalizePath(pathname: string): string {
  const collapsed = pathname.replaceAll(/\/+/g, "/");
  return collapsed.length > 1 ? collapsed.replace(/\/$/, "") : "/";
}

// Same shape as fumadocs' DefaultFormatter (which is basePath-aware - it reads
// `url.basePath`), wrapped so both directions run through normalizePath. Note
// that passing a plain *function* as `format` instead would silently drop
// basePath, because that path only overrides `add` and omits `url.basePath`.
const formatter: typeof DefaultFormatter = {
  get: DefaultFormatter.get,
  add(url, locale) {
    const next = DefaultFormatter.add(url, locale);
    next.pathname = normalizePath(next.pathname);
    return next;
  },
  remove(url) {
    const next = DefaultFormatter.remove(url);
    next.pathname = normalizePath(next.pathname);
    return next;
  },
};

export default createI18nMiddleware({ ...i18n, format: formatter });

export const config = {
  // Exclude machine-facing endpoints from the i18n locale rewrite. Without
  // this, `/sitemap.xml` is rewritten to `/en/sitemap.xml`, which has no route
  // and 404s - the same failure that hid `/llms-full.txt`. Anything served by a
  // file convention at the app root, or carrying its own `lang` segment,
  // belongs in this list.
  matcher: [
    // The docs root. It needs its own entry: the pattern below compiles to a
    // path-to-regexp group that must match at least one character, so a bare
    // "/" (which is what `/docs` becomes once basePath is stripped) slips past
    // it. Without this the index 404s while every deeper page resolves.
    "/",
    "/((?!api|_next/static|_next/image|favicon.ico|icon-light.png|icon-dark.png|og/|llms-full.txt|llms.txt|llms.mdx/|sitemap.xml|robots.txt).*)",
  ],
};
