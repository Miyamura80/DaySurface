/**
 * AI / agent discoverability content, generated from `config/landing.ts`.
 *
 * One source of truth (the landing config) drives every machine-readable
 * surface: llms.txt, llms-full.txt, agents.md, connect.md, compare.md,
 * vs/<id>.md, api.md, auth.md, pricing.md, skills.sh and the 404 body.
 * Rebranding the site (editing landing.ts) keeps all of these in sync.
 *
 * This barrel keeps `import { ... } from "../agent/content"` working for every
 * page route; the builders live in the sibling modules, split by surface:
 *
 *   _shared.ts   - helpers used by more than one builder
 *   connect.ts   - connect.md + the 404 body
 *   llms.ts      - llms.txt, llms-full.txt, agents.md
 *   pages.ts     - product.md, story.md
 *   compare.ts   - compare.md, vs/<id>.md, api.md
 *   manifests.ts - skills.sh, auth.md, pricing.md
 *   support.ts   - support.md
 *   webhooks.ts  - gmail-webhooks.md
 *   guides.ts    - connect-gmail-to-<client>.md, ai-email-triage.md
 */
export * from "./connect";
export * from "./llms";
export * from "./pages";
export * from "./compare";
export * from "./manifests";
export * from "./support";
export * from "./webhooks";
export * from "./guides";
