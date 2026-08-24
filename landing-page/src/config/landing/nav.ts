/**
 * Header nav, the primary/secondary CTAs, and the footer columns.
 */
import { site } from "./site";
import { pricing } from "./pricing";

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
    { label: "Compare", href: "/compare" },
    // Surfaced only when the page both exists (enabled) and is listed. `enabled`
    // stays the master switch, so a live-but-unlisted page (enabled && !listed)
    // is reachable by direct URL but kept out of the header, and a disabled page
    // never leaves a "Pricing" link pointing at a route that 302s home.
    ...(pricing.enabled && pricing.listed ? [{ label: "Pricing", href: "/pricing" }] : []),
    { label: "API", href: "/api" },
    { label: "Docs", href: site.docsUrl },
  ],
  // Highlighted in the header to signal the project is open source & self-hostable.
  github: {
    href: site.githubUrl,
    label: "Open source",
    title: "Open source & self-hostable, view on GitHub",
  },
  // Points at the /connect page rather than the homepage's #connect anchor: a
  // fragment is invisible to anything that fetches rather than renders, so an
  // agent following the primary CTA used to be handed the whole 272KB homepage
  // instead of the install instructions. Humans land on the same content.
  cta: { label: "Get started", href: "/connect" },
};

export const footer: { columns: FooterColumn[]; copyright: string } = {
  columns: [
    {
      heading: "Product",
      links: [
        { label: "Get started", href: "/connect" },
        { label: "Features", href: "/#features" },
        { label: "Compare", href: "/compare" },
        // Same master-switch rule as the header: linked only when the page exists
        // and is listed, so an unlisted or disabled page never gets a footer link.
        ...(pricing.enabled && pricing.listed ? [{ label: "Pricing", href: "/pricing" }] : []),
      ],
    },
    {
      heading: "Resources",
      links: [
        { label: "Docs", href: site.docsUrl },
        { label: "API Reference", href: "/api" },
        { label: "Support", href: "/support" },
        // The two editorial pages that are not per-client. Sitewide links,
        // because both were reachable from nothing but the sitemap - and the
        // footer is the cheapest inbound path a page can have. The six
        // /connect-gmail-to-<client> guides are deliberately NOT here: they are
        // linked from the compatibility strip on the homepage, where the
        // question they answer is actually being asked, and six more entries
        // would push /connect against the payload budget its test enforces.
        { label: "Gmail webhooks", href: "/gmail-webhooks" },
        { label: "AI email triage", href: "/ai-email-triage" },
        { label: "GitHub", href: site.githubUrl },
        { label: "Changelog", href: site.githubUrl + "/releases" },
      ],
    },
    {
      heading: "Company",
      links: [
        { label: "About", href: "/about" },
        { label: "Blog", href: "#" },
        { label: "Contact", href: "/contact" },
      ],
    },
    {
      heading: "Legal",
      links: [
        { label: "About", href: "/about" },
        { label: "Privacy", href: "/privacy" },
        { label: "Terms", href: "/terms" },
        { label: "Security", href: "#" },
      ],
    },
  ],
  copyright: `© ${new Date().getFullYear()} ${site.name}. All rights reserved.`,
};
