import { redirect } from "next/navigation";

// The site has no separate marketing landing page - the docs index at
// `/[lang]/docs` (rendered inside `DocsLayout`, so it carries the sidebar) is
// the landing page. Send the locale root straight there.
export default async function HomePage({
  params,
}: {
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  redirect(`/${lang}/docs`);
}
