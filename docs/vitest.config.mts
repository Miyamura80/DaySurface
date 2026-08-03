import { defineConfig } from "vitest/config";

// Scoped to `lib/` on purpose. The suite covers pure helpers - today
// `mdx-to-markdown.ts`, the `string -> string` converter behind the agent-facing
// `.mdx` twins and `llms-full.txt`. Anything that needs the Next.js runtime or
// the `fumadocs-mdx:collections/*` virtual modules belongs in the site build,
// not here.
export default defineConfig({
  test: {
    include: ["lib/**/*.test.ts"],
    environment: "node",
  },
});
