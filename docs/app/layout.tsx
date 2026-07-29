import "./global.css";
import type { Metadata } from "next";

// Title and description mirror the landing page's `site` config
// (landing-page/src/config/landing/site.ts) so search results and link
// previews describe the same product on both sites.
export const metadata: Metadata = {
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
