/**
 * Single source of truth for the landing page.
 *
 * This page is data-driven: editing the values in the modules below re-skins
 * the entire site. Swapping in a real product should be a config edit, not a
 * rewrite. Optional sections (testimonials, pricing) are gated by `enabled`
 * flags. Search for `TODO` across this directory to find every placeholder you
 * must replace.
 *
 * The config is split by section for readability; this barrel re-exports every
 * symbol so consumers keep importing from `../config/landing` unchanged.
 */
export * from "./site";
export * from "./hero";
export * from "./nav";
export * from "./connect";
export * from "./routes";
export * from "./comparison";
export * from "./pricing";
export * from "./pricing-matrix";
export * from "./content";
export * from "./product";
export * from "./story";
export * from "./support";
export * from "./webhooks";
export * from "./client-guides";
export * from "./triage";
