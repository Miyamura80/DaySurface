import { describe, expect, it } from "vitest";
import { BLOCKED_IMG_PLACEHOLDER, sanitizeEmailHtml } from "./remoteImages";

describe("sanitizeEmailHtml - sanitization (hook must not weaken DOMPurify)", () => {
  it("still strips scripts and event handlers", () => {
    const { html } = sanitizeEmailHtml(
      '<p>hi</p><script>alert(1)</script><img src="x" onerror="alert(1)">',
    );
    expect(html.toLowerCase()).not.toContain("<script");
    expect(html.toLowerCase()).not.toContain("onerror");
  });

  it("preserves safe formatting and links", () => {
    const { html } = sanitizeEmailHtml(
      '<p style="color:red">hi <a href="https://example.com" target="_blank">link</a></p>',
    );
    // CSSOM re-serialization may normalize spacing.
    expect(html).toMatch(/style="color:\s*red;?"/);
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('target="_blank"');
  });
});

describe("sanitizeEmailHtml - remote image blocking (default-deny)", () => {
  it("replaces remote img src with a placeholder and records the URL", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<p>hi</p><img src="https://cdn.example.com/logo.png" alt="logo">',
    );
    expect(remoteUrls).toEqual(["https://cdn.example.com/logo.png"]);
    expect(html).toContain(`src="${BLOCKED_IMG_PLACEHOLDER}"`);
    expect(html).not.toContain("cdn.example.com");
  });

  it("leaves data: and cid: image srcs untouched", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<img src="data:image/gif;base64,R0lGOD"><img src="cid:inline-1">',
    );
    expect(remoteUrls).toEqual([]);
    expect(html).toContain('src="data:image/gif;base64,R0lGOD"');
    expect(html).toContain('src="cid:inline-1"');
  });

  it("blocks protocol-relative URLs and normalizes them to https", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<img src="//tracker.example.com/p.gif">',
    );
    expect(remoteUrls).toEqual(["https://tracker.example.com/p.gif"]);
    expect(html).not.toContain("tracker.example.com");
  });

  it("strips relative and unknown-scheme srcs without recording them", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<img src="pixel.gif"><img src="ftp://x.example/y.png">',
    );
    expect(remoteUrls).toEqual([]);
    expect(html).not.toContain("pixel.gif");
    expect(html).not.toContain("ftp://");
  });

  it("blocks <picture><source srcset> tracking bypass", () => {
    const { html } = sanitizeEmailHtml(
      '<picture><source srcset="https://tracker.example/p.gif 1x"><img src="data:image/gif;base64,R0lGOD"></picture>',
    );
    expect(html).not.toContain("tracker.example");
    expect(html).not.toContain("srcset");
  });

  it("blocks video poster and input[type=image] src", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<video poster="https://t.example/poster.jpg"></video>' +
        '<input type="image" src="https://t.example/btn.png">',
    );
    expect(html).not.toContain("t.example");
    expect(remoteUrls).toEqual([
      "https://t.example/poster.jpg",
      "https://t.example/btn.png",
    ]);
  });

  it("neutralizes legacy background attributes", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<table background="https://a.example/bg.png"><tr><td>x</td></tr></table>',
    );
    expect(html).not.toContain("a.example");
    expect(remoteUrls).toEqual(["https://a.example/bg.png"]);
  });

  it("strips non-image media srcs without recording them in remoteUrls", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<video src="https://t.example/clip.mp4"></video>' +
        '<audio src="https://t.example/track.mp3"></audio>' +
        '<input type="text" src="https://t.example/inert.png">' +
        '<img src="https://t.example/logo.png">',
    );
    // Only the image is fetchable through the image proxy; media and inert
    // controls are removed but must not inflate the banner count.
    expect(remoteUrls).toEqual(["https://t.example/logo.png"]);
    expect(html).not.toContain("clip.mp4");
    expect(html).not.toContain("track.mp3");
    expect(html).not.toContain("inert.png");
  });

  it("closes the CSS-escape bypass (u\\72l parses as url)", () => {
    const { html } = sanitizeEmailHtml(
      '<div style="background:u\\72l(\'https://evil.example/p.gif\')">x</div>',
    );
    expect(html).not.toContain("evil.example");
  });

  it("strips image-set() references", () => {
    const { html } = sanitizeEmailHtml(
      "<div style=\"background-image:image-set('https://t.example/x.png' 1x)\">x</div>",
    );
    expect(html).not.toContain("t.example");
  });

  it("drops a style property mixing data: and remote urls", () => {
    const { html } = sanitizeEmailHtml(
      '<div style="background:url(data:image/gif;base64,R0lGOD), url(https://t.example/x.png)">x</div>',
    );
    expect(html).not.toContain("t.example");
  });

  it("scrubs remote url() from inline styles but keeps data: urls", () => {
    const { html } = sanitizeEmailHtml(
      "<div style=\"color:red;background-image:url('https://a.example/bg.jpg')\">x</div>" +
        '<div style="background-image:url(data:image/gif;base64,R0lGOD)">y</div>',
    );
    expect(html).not.toContain("a.example");
    // CSSOM re-serialization may normalize spacing and quote the URI (the
    // quote then HTML-escapes to &quot; in the serialized attribute).
    expect(html).toMatch(/color:\s*red/);
    expect(html).toMatch(/url\((&quot;|["'])?data:image\/gif/);
  });

  it("dedupes repeated URLs in remoteUrls", () => {
    const url = "https://t.example.com/pixel.gif";
    const { remoteUrls } = sanitizeEmailHtml(`<img src="${url}"><img src="${url}">`);
    expect(remoteUrls).toEqual([url]);
  });
});

describe("sanitizeEmailHtml - resolving fetched images", () => {
  it("swaps resolved URLs in as data: URIs", () => {
    const url = "https://cdn.example.com/logo.png";
    const { html } = sanitizeEmailHtml(
      `<img src="${url}">`,
      new Map([[url, "data:image/png;base64,AAAA"]]),
    );
    expect(html).toContain('src="data:image/png;base64,AAAA"');
  });

  it("keeps the placeholder for unresolved URLs and still reports them", () => {
    const { html, remoteUrls } = sanitizeEmailHtml(
      '<img src="https://a.example/ok.png"><img src="https://a.example/broken.png">',
      new Map([["https://a.example/ok.png", "data:image/png;base64,AAAA"]]),
    );
    expect(html).toContain('src="data:image/png;base64,AAAA"');
    expect(html).toContain(`src="${BLOCKED_IMG_PLACEHOLDER}"`);
    expect(remoteUrls).toContain("https://a.example/broken.png");
  });

  it("restores background attributes and protocol-relative srcs via the normalized key", () => {
    const { html } = sanitizeEmailHtml(
      '<table background="https://a.example/bg.png"><tr><td>x</td></tr></table>' +
        '<img src="//a.example/logo.png">',
      new Map([
        ["https://a.example/bg.png", "data:image/png;base64,BBBB"],
        ["https://a.example/logo.png", "data:image/png;base64,CCCC"],
      ]),
    );
    expect(html).toContain('background="data:image/png;base64,BBBB"');
    expect(html).toContain('src="data:image/png;base64,CCCC"');
  });

  it("refuses non-data:image values from the resolved map (defense in depth)", () => {
    // The hook runs after DOMPurify's own URI checks, so it must validate
    // what it writes back: a poisoned map entry stays blocked.
    const url = "https://a.example/x.png";
    const { html } = sanitizeEmailHtml(
      `<img src="${url}">`,
      new Map([[url, "javascript:alert(1)"]]),
    );
    expect(html).not.toContain("javascript:");
    expect(html).toContain(`src="${BLOCKED_IMG_PLACEHOLDER}"`);
  });
});
