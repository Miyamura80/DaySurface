import { getFaq } from "@/lib/faq";

/**
 * Renders the FAQ block for a docs page from `lib/faq.ts`.
 *
 * Deliberately always-visible markup rather than fumadocs' `Accordion`: a Radix
 * accordion unmounts closed panels, so the answers would be absent from the
 * server-rendered HTML - which both defeats the point of matching long-tail
 * queries and breaks the structured-data rule that a `FAQPage` answer must
 * appear in the visible page.
 *
 * Headings are `h3` for document outline, but they will not appear in the page
 * table of contents: fumadocs builds the TOC from the MDX AST at compile time
 * and cannot see inside a React component.
 */
export function Faq({ page }: { page: string }): React.ReactElement {
  const entries = getFaq(page);

  // Fail the build on a typo'd key rather than silently rendering nothing -
  // a missing FAQ section is invisible in review but costs the page its
  // long-tail coverage.
  if (!entries || entries.length === 0) {
    throw new Error(
      `<Faq page="${page}" /> has no entries in lib/faq.ts. ` +
        `Add them there so the visible copy and the JSON-LD stay in sync.`,
    );
  }

  return (
    <div>
      {entries.map((entry) => (
        <div key={entry.question}>
          <h3>{entry.question}</h3>
          <p>{entry.answer}</p>
        </div>
      ))}
    </div>
  );
}
