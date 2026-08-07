/**
 * Helpers shared by the agent-surface builders in this directory.
 *
 * Private to `src/agent/` - consumers import the builders from `./content`,
 * which re-exports every public surface.
 */
import { site, agentGuide, type FaqItem } from "../config/landing";

/** Strip a trailing slash so we can safely append paths. */
export function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/** Full "When to use" section shared by the long-form surfaces (llms-full.txt, agents.md). */
export function whenToUseSection(): string {
  return `## When to use

${agentGuide.summary}

Use ${site.name} when:
${agentGuide.whenToUse.map((s) => `- ${s}`).join("\n")}

Do not use ${site.name} when:
${agentGuide.whenNotToUse.map((s) => `- ${s}`).join("\n")}`;
}

/** Indent a block by two spaces, leaving blank lines genuinely blank. */
export function indent(text: string): string {
  return text
    .split("\n")
    .map((l) => (l.trim() === "" ? "" : `  ${l}`))
    .join("\n");
}

/**
 * A markdown `### question` / answer block per FAQ item.
 *
 * Three builders wrote this same map-and-join inline. The heading level is
 * fixed at `###` because every caller puts it under an `## FAQ`-style heading;
 * a builder that needs another depth should say so rather than this growing a
 * parameter for one hypothetical case.
 */
export function faqSection(items: readonly FaqItem[]): string {
  return items.map((f) => `### ${f.q}\n${f.a}`).join("\n\n");
}
