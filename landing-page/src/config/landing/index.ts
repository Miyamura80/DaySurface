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
export * from "./get-started";
export * from "./comparison";
export * from "./content";
