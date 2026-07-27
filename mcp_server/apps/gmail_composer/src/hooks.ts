import { useEffect, useLayoutEffect, useState } from "react";

// Tracks whether the app is rendered in a narrow (mobile) viewport. Guards
// against `matchMedia` being unavailable (jsdom in tests) by falling back to
// desktop behavior, so the thread stays expanded and the body keeps its inner
// scroll there - exactly the "desktop is fine" case.
export function useIsMobile(query = "(max-width: 600px)"): boolean {
  const getMatch = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false;
  const [matches, setMatches] = useState(getMatch);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    // Safari < 14 (older iOS) has no addEventListener on MediaQueryList and
    // only implements the legacy addListener/removeListener API. Calling the
    // missing method would throw at mount, so fall back when it's absent.
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, [query]);
  return matches;
}

// Grows a textarea to fit its content while `enabled`, so there is no inner
// scroll region for a touch drag to fight. Recomputes on three triggers:
//   - `value` changes (typing / an agent rewriting the body),
//   - `enabled` flips (crossing the mobile breakpoint),
//   - the element's own width changes (host iframe resized *without* crossing
//     the breakpoint - otherwise the height would go stale and, because the
//     mobile style hides overflow, the extra lines would be unreachable).
// Uses useLayoutEffect so the box is sized before paint, avoiding a one-frame
// clip on each keystroke. When disabled it clears the inline height so the
// CSS fixed-height (desktop) box takes back over.
export function useAutoGrow(
  ref: React.RefObject<HTMLTextAreaElement | null>,
  value: string,
  enabled: boolean,
) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!enabled) {
      el.style.height = "";
      return;
    }
    const resize = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    resize();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, value, enabled]);
}
