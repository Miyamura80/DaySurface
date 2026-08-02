// Playwright smoke test: render an MCP App from fixtures in a real browser and
// assert it painted. This is the automated version of the manual preview - it
// covers the "rendered iframe E2E" gap (issue #37) without needing a live MCP
// server, Gmail, or OAuth.
//
//   APP=gmail_inbox bun run smoke.mjs
//
// Not part of `make ci` (needs a browser). In the Claude Code cloud sandbox a
// Chromium is preinstalled under /opt/pw-browsers; elsewhere run
// `bunx playwright install chromium` once.
import { chromium } from "playwright";
import { existsSync, globSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = process.env.APP || process.argv[2] || "gmail_inbox";
const preview = join(HERE, "dist", `${APP}-preview.html`);

if (!existsSync(preview)) {
  console.error(`✗ ${preview} not found. Build it first: make preview_app APP=${APP}`);
  process.exit(1);
}

// Prefer a preinstalled sandbox Chromium; fall back to Playwright's default.
function chromiumPath() {
  if (process.env.PREVIEW_CHROMIUM) return process.env.PREVIEW_CHROMIUM;
  const hits = globSync("/opt/pw-browsers/chromium-*/chrome-linux/chrome");
  return hits[0];
}

const exe = chromiumPath();
const browser = await chromium.launch({
  ...(exe ? { executablePath: exe } : {}),
  args: ["--no-sandbox", "--allow-file-access-from-files"],
});
const page = await browser.newPage({ viewport: { width: 1000, height: 720 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

await page.goto("file://" + preview, { waitUntil: "load" });
const ready = await page
  .waitForFunction(() => window.__READY__ === true, { timeout: 20000 })
  .then(() => true)
  .catch(() => false);
await page.waitForTimeout(1200);

// App-specific marker that only appears once the fixture data actually
// rendered - guards against a "connected but blank / waiting" false pass.
const MARKER = {
  gmail_inbox: "Curated inbox",
  pdf_signer: "Awaiting your signature",
};
const marker = MARKER[APP] ?? "";
const frame = page.frames().find((f) => f !== page.mainFrame());
const text = frame ? await frame.evaluate(() => document.body?.innerText ?? "") : "";
const painted = marker ? text.includes(marker) : text.trim().length > 0;

await browser.close();

const failed = errors.length > 0 || !ready || !painted;
console.log(
  `${failed ? "✗" : "✓"} ${APP}: ready=${ready} painted=${painted}` +
    `${marker ? ` (marker="${marker}")` : ""} pageErrors=${errors.length}`,
);
if (errors.length) console.log("  " + errors.slice(0, 5).join("\n  "));
process.exit(failed ? 1 : 0);
