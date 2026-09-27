import { useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "system";

// index.html reads the same key before first paint, so keep them in step.
const KEY = "oi-theme";

function systemDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function readPref(): ThemePref {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function apply(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "system" && systemDark());
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

/** The light / dark / follow-the-system preference, applied to <html>. */
export function useTheme(): [ThemePref, (pref: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(readPref);

  useEffect(() => {
    apply(pref);
    try {
      if (pref === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, pref);
    } catch { /* storage unavailable: the choice lasts for this session */ }
    if (pref !== "system" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => apply("system");
    query.addEventListener?.("change", follow);
    return () => query.removeEventListener?.("change", follow);
  }, [pref]);

  return [pref, setPref];
}
