import "./global.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "MCP Template Documentation",
  description: "Batteries-included Python template with CLI, MCP, and REST API interfaces",
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
