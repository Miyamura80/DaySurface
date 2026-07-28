import "./global.css";
import type { Metadata } from "next";
import { SITE_NAME, SITE_URL } from "@/lib/site";

// `metadataBase` is what lets every page emit absolute canonical/OG URLs from a
// relative path. Without it Next warns and falls back to localhost, which is how
// canonical tags and OG images silently break in production.
export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: `${SITE_NAME} docs - Gmail MCP server setup, tools, and API`,
    template: `%s | ${SITE_NAME} docs`,
  },
  description:
    "Documentation for DaySurface, an MCP server for Gmail. Connect Claude, ChatGPT, or any MCP client to triage your inbox, draft replies, and fill and sign PDF attachments.",
  applicationName: SITE_NAME,
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
