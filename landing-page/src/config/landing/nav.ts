/**
 * Header nav, the primary/secondary CTAs, and the footer columns.
 */
import { site } from "./site";
import { pricing } from "./content";

export interface NavLink {
  label: string;
  href: string;
}

export interface FooterColumn {
  heading: string;
  links: NavLink[];
}

export const nav: {
  links: NavLink[];
  github: { href: string; label: string; title: string };
  cta: NavLink;
} = {
  links: [
    // Absolute anchors (with leading "/") so they also work from sub-pages
    // like /compare and /vs/* - a bare "#features" would only resolve on home.
    { label: "Features", href: "/#features" },
    { label: "How it works", href: "/#how-it-works" },
    { label: "Compare", href: "/compare" },
    { label: "API", href: "/api" },
    { label: "Docs", href: site.docsUrl },
  ],
  // Highlighted in the header to signal the project is open source & self-hostable.
  github: {
    href: site.githubUrl,
    label: "Open source",
    title: "Open source & self-hostable, view on GitHub",
  },
  cta: { label: "Get started", href: "/#how-it-works" },
};

export const footer: { columns: FooterColumn[]; copyright: string } = {
  columns: [
    {
      heading: "Product",
      links: [
        { label: "Features", href: "/#features" },
        { label: "How it works", href: "/#how-it-works" },
        { label: "Compare", href: "/compare" },
        // The #pricing section only renders when pricing.enabled - don't link a dead anchor otherwise.
        // Absolute (/#pricing) so it also resolves from sub-pages like /compare and /vs/*.
        ...(pricing.enabled ? [{ label: "Pricing", href: "/#pricing" }] : []),
      ],
    },
    {
      heading: "Resources",
      links: [
        { label: "Docs", href: site.docsUrl },
        { label: "API Reference", href: "/api" },
        { label: "GitHub", href: site.githubUrl },
        { label: "Changelog", href: site.githubUrl + "/releases" },
      ],
    },
    {
      heading: "Company",
      links: [
        { label: "About", href: "#" },
        { label: "Blog", href: "#" },
        { label: "Contact", href: "#" },
      ],
    },
    {
      heading: "Legal",
      links: [
        { label: "Privacy", href: "/privacy" },
        { label: "Terms", href: "/terms" },
        { label: "Security", href: "#" },
      ],
    },
  ],
  copyright: `© ${new Date().getFullYear()} ${site.name}. All rights reserved.`,
};
