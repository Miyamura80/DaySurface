// Turn the raw MDX body into clean Markdown for llms.txt / llms-full.txt.
//
// We deliberately avoid fumadocs' "processed" text here: its heading handler
// drops the `#` depth markers (a `## Section` becomes a bare `Section [#slug]`
// line) and it leaves JSX imports/components inline, so the resulting file has
// no real Markdown structure for an LLM to follow. Working from "raw" lets us
// preserve `##`/`###` sections, fenced code blocks, and inline links, while
// converting the handful of fumadocs UI components we use into plain Markdown.
//
// This lives apart from `source.ts` so it can be unit-tested: `source.ts` pulls
// in `fumadocs-mdx:collections/server`, a virtual module that only exists after
// the site's codegen step, which would drag a whole build into a test of a pure
// `string -> string` function.
//
// Maintained in parallel with the same file in Miyamura80/MCP-Template. The two
// copies are byte-identical on purpose; a fix here wants porting there, and vice
// versa. Keep the comments describing shapes rather than repo-specific files so
// they stay true on both sides.

export function mdxBodyToMarkdown(raw: string): string {
  const withoutFrontmatter = raw
    // Strip the leading YAML frontmatter (title/description are re-added below).
    .replace(/^---\n[\s\S]*?\n---\n?/, "");

  // Rewrite prose only. None of the rules in `stripMdxSyntax` mean anything
  // inside a fence, and applying them there produced code that cannot run: the
  // `import`/`export` rule was deleting the imports off the Python examples and
  // `export FOO=...` from shell ones.
  const rewritten = splitFences(withoutFrontmatter).map((segment) =>
    segment.code ? segment : { code: false, text: stripMdxSyntax(segment.text) },
  );

  return joinSegments(rewritten).trim();
}

/**
 * Reassemble the segments, collapsing blank runs that straddle a seam.
 *
 * The per-segment collapse in `stripMdxSyntax` cannot see these. A prose block
 * ending in two blank lines is only `\n\n` on its own; it becomes `\n\n\n`
 * against the fence below it once the joining newline goes back, so the run
 * never matches `\n{3,}` while the segment is held separately. Trimming the
 * prose edge to one newline puts the seam at a single blank line, which is what
 * the old whole-document collapse produced, without ever touching the code.
 */
function joinSegments(segments: Segment[]): string {
  return segments
    .map((segment, i) => {
      if (segment.code) return segment.text;
      let text = segment.text;
      if (i > 0) text = text.replace(/^\n+/, "\n");
      if (i < segments.length - 1) text = text.replace(/\n+$/, "\n");
      // A prose run that is nothing but blank lines between two fences would
      // still emit two, since each edge keeps one. Drop it entirely.
      if (i > 0 && i < segments.length - 1 && text.trim() === "") text = "";
      return text;
    })
    .join("\n");
}

/** A run of consecutive lines, tagged by whether they sit inside a code fence. */
export type Segment = { code: boolean; text: string };

/**
 * Opens a fence: >=3 backticks or tildes, at any indent.
 *
 * Not `^ {0,3}`, which is how CommonMark states the rule but not what it means:
 * the 3-space allowance is measured from the containing block, so a fence inside
 * a second-level bullet or a two-digit list item sits at 4+ spaces and an
 * absolute anchor walks straight past it. The consequence is not cosmetic - an
 * unrecognised fence gets `stripMdxSyntax` applied to it, which is exactly the
 * import-eating this module exists to prevent.
 *
 * The allowance is deliberately unbounded, and that is a tradeoff rather than a
 * free win. A line scanner cannot tell a nested-list fence from a CommonMark
 * *indented* code block (4+ spaces, no fence) whose content happens to contain a
 * line of backticks - by indent alone the two are identical, and no bound
 * separates them, since indented code starts at exactly the depth nested fences
 * reach. Erring toward "it's a fence" means such a block would open one and the
 * prose after it would go unrewritten until a matching close.
 *
 * Chosen knowing that, because in these docs the shapes are not symmetric:
 * fenced blocks are the house style and indented code blocks number zero across
 * every page, while fences nested in list items are real and present. The test
 * suite pins this direction, so flipping it is a deliberate act rather than a
 * silent regression.
 */
const FENCE_OPEN = /^[ \t]*(`{3,}|~{3,})/;
/**
 * Closes one: the same run, at least as long, alone on its line. Indent is not
 * compared against the opener - a closing fence that lines up with its content
 * rather than its opener still closes the block, and being liberal here only
 * ends a code segment early, where being strict would swallow the rest of the
 * document as code.
 */
const FENCE_CLOSE = /^[ \t]*(`{3,}|~{3,})[ \t]*$/;

/**
 * Partition `body` into alternating prose and code segments.
 *
 * Scans line by line rather than pairing delimiters positionally. Splitting on
 * a `/(^```[\s\S]*?^```)/` regex and trusting the odd indices to be code
 * assumes every delimiter in the document alternates open/close, which one
 * stray ``` inside a code block - a page documenting fences - inverts for the
 * whole remainder of the file, silently swapping which half gets rewritten.
 * Tracking the open delimiter also gets the cases that shape cannot see:
 * indented fences (`docs/content/docs/cli/index.mdx` has one inside a bullet),
 * `~~~` fences, and fences longer than three characters.
 *
 * Reassembly is exact - the segments partition the lines in order, so joining
 * them with the newline that separated them reproduces the input.
 */
export function splitFences(body: string): Segment[] {
  const segments: Segment[] = [];
  let buffer: string[] = [];
  /** The delimiter that opened the current fence, or null when in prose. */
  let openedBy: string | null = null;

  const flush = (code: boolean) => {
    if (buffer.length > 0) segments.push({ code, text: buffer.join("\n") });
    buffer = [];
  };

  for (const line of body.split("\n")) {
    if (openedBy === null) {
      const opening = line.match(FENCE_OPEN)?.[1];
      if (opening) {
        flush(false);
        openedBy = opening;
      }
      buffer.push(line);
      continue;
    }

    buffer.push(line);
    const closing = line.match(FENCE_CLOSE)?.[1];
    if (closing && closing[0] === openedBy[0] && closing.length >= openedBy.length) {
      flush(true);
      openedBy = null;
    }
  }

  // An unterminated fence still holds code. Treating the tail as prose would
  // strip exactly the lines the fence was protecting.
  flush(openedBy !== null);
  return segments;
}

/**
 * The attribute run inside a JSX tag, up to but not including its closing `>`.
 *
 * Not `[^>]*`: that stops at the first `>` anywhere, including one inside a
 * quoted value. `<Callout title="Compare A > B">` would then match only as far
 * as the inner `>`, the `title=` lookup would miss its closing quote, and the
 * orphaned `B">` tail would be emitted into the page as raw text. Consuming
 * quoted values whole avoids it.
 */
const TAG_ATTRS = String.raw`(?:[^>"']|"[^"]*"|'[^']*')*`;

const CARD_TAG = new RegExp(String.raw`<Card\b${TAG_ATTRS}>`, "g");
const TAB_TAG = new RegExp(String.raw`<Tab\b${TAG_ATTRS}>`, "g");
const CALLOUT_TAG = new RegExp(String.raw`<Callout\b${TAG_ATTRS}>`, "g");
const STRUCTURAL_TAG = new RegExp(
  String.raw`</?(?:Cards|Steps|Step|Tabs|Tab|Callout)\b${TAG_ATTRS}>`,
  "g",
);

/**
 * Read one attribute's value out of a matched tag.
 *
 * Two things the obvious `/\bname=["']([^"']*)["']/` gets wrong, both silent:
 *
 * - `[^"']` ends the value at the first quote of *either* kind, so a
 *   double-quoted title containing an apostrophe is truncated - `title="Don't
 *   do this"` yields `Don`. Callout titles are prose headings, so apostrophes
 *   are ordinary; matching each quote style against its own closer fixes it and
 *   also stops `title="X'` from matching at all.
 * - `\b` looks like it anchors the attribute name, but `-` is a non-word
 *   character, so the boundary is satisfied mid-token and `data-title="Decoy"`
 *   matches `title=`. Requiring whitespace or the tag's start actually anchors it.
 */
function attrValue(tag: string, name: string): string | undefined {
  const match = tag.match(
    new RegExp(String.raw`(?:^|\s)${name}=(?:"([^"]*)"|'([^']*)')`),
  );
  return match?.[1] ?? match?.[2];
}

/** The MDX-to-Markdown rewrites, applied to prose only - never to code. */
function stripMdxSyntax(prose: string): string {
  return (
    prose
      // Drop MDX `import`/`export` statements.
      .replace(/^\s*(?:import|export)\s.+$/gm, "")
      // Drop JSX expression-container props (e.g. `icon={<Rocket />}`). The `>`
      // inside a nested component is not quoted, so `TAG_ATTRS` cannot absorb it
      // and the tag scans below would still terminate early. We only emit
      // title/href, so these props are noise for the LLM text anyway.
      .replace(/\s+[A-Za-z_][\w-]*=\{[^}]*\}/g, "")
      // <Card title="X" href="Y" /> -> a Markdown link to the related resource.
      .replace(CARD_TAG, (tag) => {
        const title = attrValue(tag, "title");
        const href = attrValue(tag, "href");
        if (title && href) return `- [${title}](${href})`;
        if (title) return `- ${title}`;
        return "";
      })
      // <Tab value="X"> -> a bold label so per-tab content stays attributed.
      .replace(TAB_TAG, (tag) => {
        const value = attrValue(tag, "value");
        return value ? `\n**${value}**\n` : tag;
      })
      // <Callout title="X"> -> a bold label. The title carries the point of the
      // callout ("Forking this repo?", "Account requirements"); dropping it with
      // the tag leaves the body floating with nothing to attach it to.
      .replace(CALLOUT_TAG, (tag) => {
        const title = attrValue(tag, "title");
        return title ? `\n**${title}**\n` : "";
      })
      // MDX block comments, which may span lines. They never carry reader-facing
      // content - an authoring note or a generated-region marker - so stripping
      // them keeps that out of the twin published to LLM consumers.
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
      // Strip the remaining structural component tags, keeping their children.
      .replace(STRUCTURAL_TAG, "")
      // Collapse the blank lines left behind by the removals. Prose-only, like
      // the rest: run over the whole document it also reflows code, and the
      // blank line PEP 8 wants before a top-level `def` is exactly the run it
      // eats.
      .replace(/\n{3,}/g, "\n\n")
  );
}
