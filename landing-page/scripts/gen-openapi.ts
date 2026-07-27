/**
 * Snapshot the deployed OpenAPI spec into `public/openapi.json` at build time.
 *
 * Runs before `astro build` (see package.json `build`). The committed snapshot
 * is what the `/api` reference page (Scalar) renders, served same-origin from
 * `daysurface.com/openapi.json` - so the page has no runtime dependency on the
 * API host being up or CORS-readable from the landing origin.
 *
 * Strategy:
 *   1. Fetch `${site.apiUrl}/openapi.json` (server-to-server, no CORS).
 *   2. Normalize: force the `servers` block + branded `info` from `landing.ts`
 *      so the snapshot is correct even if the live backend predates the
 *      `API_PUBLIC_URL` change.
 *   3. Write `public/openapi.json`.
 *
 * If the fetch fails (API down, offline build) we KEEP the existing committed
 * snapshot rather than overwrite it with garbage, and exit 0 so the build still
 * succeeds. Commit the refreshed snapshot when the API surface changes.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { site } from "../src/config/landing";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "..", "public", "openapi.json");
const specUrl = new URL("/openapi.json", site.apiUrl).href;

function normalize(spec: Record<string, unknown>): Record<string, unknown> {
  const info = (spec.info ?? {}) as Record<string, unknown>;
  return {
    ...spec,
    // Same-origin snapshot still describes the real API host for "Try it out".
    servers: [{ url: site.apiUrl, description: `${site.name} production API` }],
    info: {
      ...info,
      title: `${site.name} API`,
      description: site.description,
    },
  };
}

async function fetchSpec(): Promise<Record<string, unknown> | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const res = await fetch(specUrl, { signal: controller.signal });
    if (!res.ok) {
      console.warn(`⚠ ${specUrl} returned ${res.status}; keeping snapshot`);
      return null;
    }
    return (await res.json()) as Record<string, unknown>;
  } catch (err) {
    console.warn(`⚠ could not fetch ${specUrl} (${String(err)}); keeping snapshot`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const live = await fetchSpec();
if (live) {
  writeFileSync(out, JSON.stringify(normalize(live), null, 2) + "\n");
  console.log(`✓ snapshotted public/openapi.json from ${specUrl}`);
} else if (existsSync(out)) {
  // Re-normalize the committed snapshot so branding/servers stay in sync with
  // landing.ts even on an offline build.
  const snap = JSON.parse(readFileSync(out, "utf8")) as Record<string, unknown>;
  writeFileSync(out, JSON.stringify(normalize(snap), null, 2) + "\n");
  console.log("✓ kept existing public/openapi.json snapshot (re-normalized)");
} else {
  console.error(
    `✗ no live spec and no committed snapshot at ${out}; run with the API reachable once to seed it`,
  );
  process.exit(1);
}
