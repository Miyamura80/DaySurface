import { describe, expect, it } from "vitest";

import { mdxBodyToMarkdown, splitFences } from "./mdx-to-markdown";

/**
 * The cases below are the ones that actually broke, or that the line scanner
 * exists to survive. Two shipped as defects in two consecutive commits - the
 * `import`/`export` rule eating lines inside code fences, and the blank-line
 * collapse reflowing Python - so the point of this file is that a regression in
 * either is a red build rather than a review catch.
 */

/** Which lines a segment list classified as code, flattened for assertions. */
function codeLines(body: string): string[] {
  return splitFences(body)
    .filter((segment) => segment.code)
    .flatMap((segment) => segment.text.split("\n"));
}

describe("splitFences", () => {
  it("puts every line in exactly one segment, in order", () => {
    const body = ["prose", "```py", "x = 1", "```", "more prose"].join("\n");

    expect(splitFences(body).map((s) => s.text).join("\n")).toBe(body);
  });

  it("does not let a nested fence invert the rest of the document", () => {
    // A four-backtick block whose body contains a ``` fence. Pairing
    // delimiters positionally would treat the inner ``` as a close and the
    // following prose as code for the remainder of the file.
    const body = [
      "````md",
      "```python",
      "import os",
      "```",
      "````",
      "",
      "import { Card } from 'fumadocs-ui/components/card';",
    ].join("\n");

    expect(codeLines(body)).toEqual([
      "````md",
      "```python",
      "import os",
      "```",
      "````",
    ]);
    // The MDX import after the block is prose again, so it still gets stripped.
    expect(mdxBodyToMarkdown(body)).not.toContain("fumadocs-ui/components/card");
  });

  it("sees a fence indented inside a bullet", () => {
    // `docs/content/docs/cli/index.mdx` has one of these, in the bullet about
    // piping shell values.
    const body = [
      "- Pipe it:",
      "",
      "  ```bash",
      "  export TOKEN=$(daysurface auth token)",
      "  ```",
    ].join("\n");

    expect(codeLines(body)).toContain("  export TOKEN=$(daysurface auth token)");
    expect(mdxBodyToMarkdown(body)).toContain("export TOKEN=$(daysurface auth token)");
  });

  it("sees a fence indented 4+ spaces, inside a nested list", () => {
    // CommonMark's 3-space allowance is relative to the containing block, so a
    // fence under a second-level bullet sits at 4+. An absolute `^ {0,3}` misses
    // it and strips the code inside.
    const body = [
      "1. Add the service.",
      "2. Add a row:",
      "",
      "        ```python",
      "        import os",
      "        ```",
    ].join("\n");

    expect(codeLines(body)).toContain("        import os");
    expect(mdxBodyToMarkdown(body)).toContain("import os");
  });

  it("treats a fence line inside an indented block as opening a fence", () => {
    // Pins the tradeoff `FENCE_OPEN` documents. By indent alone a nested-list
    // fence and a CommonMark indented code block are identical, so recognising
    // the first means also recognising the second. These docs use fenced blocks
    // throughout and contain no indented code blocks, so this is the direction
    // that loses nothing - but it is a choice, and flipping it should be
    // deliberate rather than a silent regression.
    const body = [
      "Prose.",
      "",
      "    ```",
      "    still code",
      "    ```",
      "",
      "import { Card } from 'fumadocs-ui/components/card';",
    ].join("\n");

    expect(codeLines(body)).toContain("    still code");
    // The cost is bounded by the block closing: prose after it is prose again,
    // so the rules resume. Only an *unmatched* fence inside such a block would
    // swallow the tail, which is the same exposure any unterminated fence has.
    expect(mdxBodyToMarkdown(body)).not.toContain("fumadocs-ui/components/card");
  });

  it("handles ~~~ fences", () => {
    const body = ["~~~python", "import os", "~~~", "", "prose"].join("\n");

    expect(codeLines(body)).toEqual(["~~~python", "import os", "~~~"]);
  });

  it("does not close a fence with a shorter run, or the other character", () => {
    const body = [
      "`````python",
      "```",
      "~~~~~",
      "import os",
      "`````",
      "",
      "prose",
    ].join("\n");

    expect(codeLines(body)).toEqual([
      "`````python",
      "```",
      "~~~~~",
      "import os",
      "`````",
    ]);
  });

  it("treats an unterminated fence at EOF as code", () => {
    // Rewriting the tail as prose would strip exactly the lines the fence was
    // opened to protect.
    const body = ["```python", "import os", "export = 1"].join("\n");

    expect(codeLines(body)).toEqual(["```python", "import os", "export = 1"]);
    expect(mdxBodyToMarkdown(body)).toContain("import os");
  });
});

describe("mdxBodyToMarkdown", () => {
  it("strips the leading frontmatter", () => {
    const raw = ["---", "title: Tools", "description: Stuff", "---", "", "Body."].join(
      "\n",
    );

    expect(mdxBodyToMarkdown(raw)).toBe("Body.");
  });

  it("keeps import and export lines inside code fences", () => {
    // The first shipped defect: the twins published Python and shell examples
    // with their imports deleted, so the snippets could not run.
    const raw = [
      "Example:",
      "",
      "```python",
      "import os",
      "from common import global_config",
      "```",
      "",
      "```bash",
      "export DAYSURFACE_API_KEY=sk_live_...",
      "```",
    ].join("\n");

    const out = mdxBodyToMarkdown(raw);
    expect(out).toContain("import os");
    expect(out).toContain("from common import global_config");
    expect(out).toContain("export DAYSURFACE_API_KEY=sk_live_...");
  });

  it("keeps the PEP 8 blank lines inside a python block", () => {
    // The second shipped defect: the blank-line collapse ran over code and ate
    // the two blank lines PEP 8 wants before a top-level `def`.
    const raw = [
      "```python",
      "import os",
      "",
      "",
      "def main():",
      "    return os.getcwd()",
      "```",
    ].join("\n");

    expect(mdxBodyToMarkdown(raw)).toContain("import os\n\n\ndef main():");
  });

  it("still strips a real MDX import from prose", () => {
    const raw = [
      "import { Card } from 'fumadocs-ui/components/card';",
      "",
      "Prose.",
    ].join("\n");

    expect(mdxBodyToMarkdown(raw)).toBe("Prose.");
  });

  it("collapses blank lines in prose but not in code", () => {
    const raw = ["A.", "", "", "", "B.", "", "```text", "x", "", "", "", "y", "```"].join(
      "\n",
    );

    const out = mdxBodyToMarkdown(raw);
    expect(out).toContain("A.\n\nB.");
    expect(out).toContain("x\n\n\n\ny");
  });

  it("strips MDX block comments so authoring notes do not ship", () => {
    // Comments are never reader-facing, so leaking one publishes an internal
    // note into the twin that LLM consumers read.
    const raw = [
      "{/* TODO: rewrite this section before launch */}",
      "",
      "Real content.",
      "",
      "{/* END */}",
    ].join("\n");

    expect(mdxBodyToMarkdown(raw)).toBe("Real content.");
  });

  it("keeps an apostrophe inside a double-quoted attribute", () => {
    // `[^"']` ends the value at either quote character, truncating the title to
    // "Don". Callout titles are prose headings, so apostrophes are ordinary.
    const raw = `<Callout title="Don't do this">\nBody.\n</Callout>`;

    expect(mdxBodyToMarkdown(raw)).toBe("**Don't do this**\n\nBody.");
  });

  it("does not read a hyphenated lookalike attribute", () => {
    // `\b` is satisfied between `-` and `t`, so `\btitle=` matches `data-title=`.
    const raw = `<Callout data-title="Decoy">\nBody.\n</Callout>`;

    expect(mdxBodyToMarkdown(raw)).toBe("Body.");
  });

  it("does not read an attribute name out of another prop's value", () => {
    // Searching for `title=` finds the one quoted inside `description`, because
    // it comes first in the string. Walking the attributes steps past whole
    // values, so only real attribute positions are considered.
    const title = `<Card description='set title="Fake"' title="Real" href="/r" />`;
    const href = `<Card description='use href="/fake"' title="Real" href="/real" />`;

    expect(mdxBodyToMarkdown(title)).toBe("- [Real](/r)");
    expect(mdxBodyToMarkdown(href)).toBe("- [Real](/real)");
  });

  it("strips a block comment spanning several lines", () => {
    // The `[\s\S]` in the comment pattern exists for this; a single-line test
    // would still pass against a narrowed `[^\n]*`.
    const raw = [
      "{/* BEGIN generated",
      "   spanning several lines",
      "   still the comment */}",
      "",
      "Real content.",
    ].join("\n");

    expect(mdxBodyToMarkdown(raw)).toBe("Real content.");
  });

  it("survives a `>` inside a quoted attribute value", () => {
    // `[^>]*` would stop at the inner `>`, miss the closing quote, and emit the
    // orphaned tail as raw text.
    const callout = '<Callout title="Compare A > B">\nBody.\n</Callout>';
    const card = '<Card title="A > B" href="/x" />';

    expect(mdxBodyToMarkdown(callout)).toBe("**Compare A > B**\n\nBody.");
    expect(mdxBodyToMarkdown(card)).toBe("- [A > B](/x)");
  });

  it("keeps a Callout title as a label", () => {
    const raw = [
      '<Callout type="warn" title="Forking this repo?">',
      "Read this first.",
      "</Callout>",
    ].join("\n");

    const out = mdxBodyToMarkdown(raw);
    expect(out).toContain("**Forking this repo?**");
    expect(out).toContain("Read this first.");
    expect(out).not.toContain("<Callout");
  });

  it("drops a Callout with no title, tag and all", () => {
    const raw = ['<Callout type="info">', "Body only.", "</Callout>"].join("\n");

    expect(mdxBodyToMarkdown(raw)).toBe("Body only.");
  });

  it("rewrites the fumadocs components it knows", () => {
    const raw = [
      "<Cards>",
      '  <Card icon={<Rocket />} title="Quickstart" href="/docs/quickstart" />',
      "</Cards>",
    ].join("\n");

    expect(mdxBodyToMarkdown(raw)).toBe("- [Quickstart](/docs/quickstart)");
  });
});
