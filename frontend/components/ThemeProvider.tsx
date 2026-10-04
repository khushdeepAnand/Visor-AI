"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";

type ThemePreference = "light" | "dark" | "system";
type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "stockpilot-theme";

interface ThemeContextValue {
  /** The theme actually applied right now - always "light" or "dark". */
  theme: ResolvedTheme;
  /** What the user asked for - may be "system", meaning "follow the OS". */
  preference: ThemePreference;
  setPreference: (preference: ThemePreference) => void;
  /** Convenience: flips between light and dark, leaving "system" behind. */
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function resolve(preference: ThemePreference): ResolvedTheme {
  if (preference === "system") {
    if (typeof window === "undefined") return "light";
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return preference;
}

/**
 * Pairs with the inline blocking script in app/layout.tsx, which sets the
 * "dark" class on <html> before first paint (so there is no flash of the
 * wrong theme) and reads the same localStorage key this provider writes to.
 * This component's job after mount is just to keep React state, the DOM
 * class, and localStorage in sync as the user changes their preference or
 * their OS preference changes underneath a "system" selection.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>("system");
  const [theme, setTheme] = useState<ResolvedTheme>("light");

  // On mount, read back whatever the blocking script already decided so we
  // never re-render into a different theme than what was already painted.
  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(STORAGE_KEY);
    } catch {
      stored = null;
    }
    const initialPreference: ThemePreference = stored === "light" || stored === "dark" ? stored : "system";
    setPreferenceState(initialPreference);
    setTheme(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);

  // Follow OS changes live, but only while the user has not made an
  // explicit choice (preference === "system").
  useEffect(() => {
    if (preference !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const next = resolve("system");
      setTheme(next);
      document.documentElement.classList.toggle("dark", next === "dark");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [preference]);

  const setPreference = (next: ThemePreference) => {
    setPreferenceState(next);
    const resolved = resolve(next);
    setTheme(resolved);
    document.documentElement.classList.toggle("dark", resolved === "dark");
    try {
      if (next === "system") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage may be unavailable (private browsing, disabled cookies).
      // The theme still applies for this session via the DOM class above.
    }
  };

  const toggle = () => setPreference(theme === "dark" ? "light" : "dark");

  const value = useMemo(() => ({ theme, preference, setPreference, toggle }), [theme, preference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
