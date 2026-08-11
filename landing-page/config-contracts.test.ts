/**
 * Config contract tests. Run with `bun test` from `landing-page/`.
 *
 * Separate from `agent-journey.test.ts` because these assert on the config
 * objects directly and never start the server - there is no route, no fetch and
 * no `dist/` involved. They exist for one recurring failure: an invariant that
 * is stated in a doc comment and enforced by nothing, which the next reader then
 * trusts. Each test below is the enforcement for a rule written in prose
 * somewhere in `src/config/landing/`.
 */
import { describe, expect, test } from "bun:test";
import { existsSync } from "node:fs";

import { clientGuides, connect, triage } from "./src/config/landing";

describe("client guides stay consistent with the install picker", () => {
  // `ClientGuide.targetId` claims to be the matching `connect.ts` target id, so
  // the two can be cross-checked. Nothing cross-checked it - the field was
  // declared, documented as a guarantee, and read by nothing, which is worse
  // than having no field at all because the next reader trusts the comment.
  test("every non-null targetId resolves to a real install target", () => {
    const ids = new Set(connect.targets.map((t) => t.id));
    const dangling = clientGuides
      .filter((g) => g.targetId !== null && !ids.has(g.targetId))
      .map((g) => `${g.slug} -> ${g.targetId}`);
    expect(dangling).toEqual([]);
  });

  // A heuristic, and labelled as one. Nothing in the data can prove a null
  // targetId is CORRECT - only that it is obviously wrong, i.e. the picker
  // plainly offers this client and someone marked it null anyway. Slug and
  // clientName are the two available signals, and both are checked because
  // `slug` is NOT required to equal `targetId`: M365 Copilot's slug is
  // "microsoft-365-copilot" while its target, had it kept one, was
  // "m365-copilot". Asserting they match would encode a rule that is only
  // coincidentally true today.
  test("targetId is not null for a client the picker plainly offers", () => {
    const ids = new Set(connect.targets.map((t) => t.id.toLowerCase()));
    const names = new Set(connect.targets.map((t) => t.name.toLowerCase()));
    const wronglyNull = clientGuides
      .filter(
        (g) =>
          g.targetId === null &&
          (ids.has(g.slug.toLowerCase()) || names.has(g.clientName.toLowerCase())),
      )
      .map((g) => g.slug);
    expect(wronglyNull).toEqual([]);
  });

  test("every guide points at a logo that ships", () => {
    for (const g of clientGuides) {
      expect(existsSync(new URL(`./public${g.logo}`, import.meta.url))).toBe(true);
    }
  });
});

describe("editorial pages keep the claims they promise", () => {
  // triage.ts opens with "NOTHING HERE MAY CARRY A NUMBER": there is no time
  // study behind /ai-email-triage, so any figure would be a guess dressed as
  // evidence, and the FAQ answers "how much time will this save" with a refusal
  // rather than a range. That was a prose warning and nothing enforced it - the
  // exact failure mode this file already guards for `targetId`. A future editor
  // adding "saves 3 hours a week" now fails here instead of shipping.
  test("/ai-email-triage carries no numeric claims", () => {
    const offenders = Object.entries(triage).flatMap(([key, value]) => {
      const text = JSON.stringify(value);
      const hit = text.match(/.{0,50}[0-9].{0,50}/);
      return hit ? [`${key}: ...${hit[0]}...`] : [];
    });
    expect(offenders).toEqual([]);
  });
});
