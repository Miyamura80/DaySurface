import "./global.css";
import type { Metadata } from "next";
import { SITE_URL } from "@/lib/site";

// Title and description mirror the landing page's `site` config
// (landing-page/src/config/landing/site.ts) so search results and link
// previews describe the same product on both sites.
export const metadata: Metadata = {
  // Without this, Next resolves relative metadata URLs against localhost and
  // warns - which is how canonical tags and OG images silently break in prod.
  metadataBase: new URL(SITE_URL),
  title: {
    default: "DaySurface Documentation",
    template: "%s | DaySurface Docs",
  },
  description:
    "An MCP server for Gmail: triage a ranked inbox, draft replies in a real composer, and fill and sign PDF attachments - inside Claude, ChatGPT, or any MCP client.",
  icons: {
    icon: [
      {
        url: "/favicon.ico",
      },
      {
        url: "/icon-light.png",
        media: "(prefers-color-scheme: light)",
      },
      {
        url: "/icon-dark.png",
        media: "(prefers-color-scheme: dark)",
      },
    ],
    apple: "/icon-light.png",
  },
};

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
