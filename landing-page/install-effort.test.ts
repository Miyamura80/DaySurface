/**
 * Install-effort classification tests. Run with `bun test` from `landing-page/`.
 *
 * These exist for one bucket in particular. No target in `connect.ts` uses
 * `method: "manual"`, so every branch of the manual path - the `effortOf`
 * classification, its `effortMeta` entry, and its rendering in llms-full.txt -
 * is otherwise dead to `make ci`. It is deliberate scaffolding (connect.ts
 * keeps the method for the next host that ships no URL scheme at all), but
 * unexercised scaffolding rots: a bug in it would surface for the first time
 * whenever a real manual host is added, a long way from the change that caused
 * it. Synthetic targets exercise it now.
 *
 * `effortOf` is exported solely for this. It is pure and argument-taking, which
 * `installMatrix` is not - the matrix is computed once at module load from the
 * real config, so a synthetic target cannot be pushed through it.
 */
import { describe, expect, test } from "bun:test";

import { effortOf, effortMeta, type InstallEffort } from "./src/lib/install";
import type { InstallTarget } from "./src/config/landing";

const target = (over: Partial<InstallTarget>): InstallTarget => ({
  id: "synthetic",
  name: "Synthetic",
  logo: "/logos/mcp.svg",
  method: "manual",
  ...over,
});

describe("effortOf classifies every install method", () => {
  test.each<[string, Partial<InstallTarget>, InstallEffort]>([
    ["deeplink that prefills", { method: "deeplink" }, "one-click"],
    ["deeplink that does not", { method: "deeplink", prefills: false }, "dialog-only"],
    ["a terminal command", { method: "prompt", setupKind: "command" }, "command"],
    ["a paste-in prompt", { method: "prompt" }, "prompt"],
    ["a hand-configured client", { method: "manual" }, "manual"],
  ])("%s", (_label, over, expected) => {
    expect(effortOf(target(over))).toBe(expected);
  });

  // The bug this bucket exists to prevent. `manual` used to fall through to
  // `prompt`, which switched off the shared-prompt collapse in
  // groupedInstallMatrix (it only fires when EVERY client in a group carries
  // the same prompt) and reprinted a ~450-char prompt once per client on
  // /connect and connect.md.
  test("a manual target never lands in the prompt bucket", () => {
    expect(effortOf(target({ method: "manual" }))).not.toBe("prompt");
  });

  // A manual target carries no prompt, so `steps` is the only instruction it
  // has. Its label has to exist, or llms-full.txt renders a bare numbered list.
  test("the manual bucket carries its own heading and steps label", () => {
    expect(effortMeta.manual.heading).toBeTruthy();
    expect(effortMeta.manual.stepsLabel).toBeTruthy();
    expect(effortMeta.manual.heading).not.toBe(effortMeta.prompt.heading);
  });
});
