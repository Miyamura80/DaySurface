import { useEffect, useState } from "react";

// Tracks whether the app is rendered in a narrow viewport (e.g. a phone-sized
// chat client). Drives the Gmail-mobile-style single-column navigation: the
// list and the reader occupy the full width and you navigate between them,
// instead of the cramped side-by-side two-pane layout used on wide screens.
export function useIsNarrow(breakpoint = 640): boolean {
  const query = `(max-width: ${breakpoint}px)`;
  const [narrow, setNarrow] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setNarrow(e.matches);
    setNarrow(mq.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [query]);
  return narrow;
}
