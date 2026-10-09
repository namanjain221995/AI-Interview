/**
 * App colour theme: light, dark, or follow the operating system ("system").
 *
 * - The preference lives in localStorage["app.theme"] (per browser, per viewer;
 *   storage failures degrade to in-memory state). Missing means "system".
 * - The resolved theme is written to <html data-theme="light|dark"> together with
 *   data-theme-preference, `color-scheme` and the theme-color meta. CSS keys only on
 *   data-theme (see styles/globals.css); the `dark:` variant follows it.
 * - public/theme-init.js applies the same rules before the first paint. Keep the
 *   storage key, values and attributes in sync with that file.
 * - The interview stage (`.theme-stage`) is always dark and ignores this setting.
 */
import { useEffect, useLayoutEffect } from "react";
import { create } from "zustand";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_PREFERENCES: readonly ThemePreference[] = ["light", "dark", "system"];
export const THEME_STORAGE_KEY = "app.theme";
/** Browser UI colour (address bar on mobile): the page canvas of each theme. */
export const THEME_COLORS: Record<ResolvedTheme, string> = { light: "#f5f7fb", dark: "#0a0e1c" };
export const DARK_SCHEME_QUERY = "(prefers-color-scheme: dark)";

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

export function readStoredPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

function writeStoredPreference(preference: ThemePreference): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Storage unavailable: the choice lasts for this page only.
  }
}

export function systemPrefersDark(): boolean {
  try {
    return typeof window.matchMedia === "function" && window.matchMedia(DARK_SCHEME_QUERY).matches;
  } catch {
    return false;
  }
}

export function resolveTheme(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
  if (preference === "system") return systemDark ? "dark" : "light";
  return preference;
}

/**
 * Writes the theme to the document. Colour transitions are suppressed for one frame
 * when the theme actually changes, so the switch is instant instead of a cascade.
 */
export function applyTheme(resolved: ResolvedTheme, preference: ThemePreference, root: HTMLElement = document.documentElement): void {
  const changed = root.getAttribute("data-theme") !== resolved;
  if (changed) root.classList.add("theme-switching");
  root.setAttribute("data-theme", resolved);
  root.setAttribute("data-theme-preference", preference);
  root.style.colorScheme = resolved;
  root.ownerDocument.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLORS[resolved]);
  if (changed) {
    // Force a style flush with transitions off, then re-enable them on the next frame.
    void root.offsetHeight;
    const raf = typeof window.requestAnimationFrame === "function" ? window.requestAnimationFrame : (cb: () => void) => window.setTimeout(cb, 0);
    raf(() => root.classList.remove("theme-switching"));
  }
}

interface ThemeState {
  preference: ThemePreference;
  systemDark: boolean;
  setPreference: (preference: ThemePreference) => void;
  /** Internal: OS colour scheme changed. */
  setSystemDark: (dark: boolean) => void;
  /** Internal: another tab changed the stored preference. */
  syncFromStorage: () => void;
}

export const useThemeStore = create<ThemeState>()((set) => ({
  preference: typeof window === "undefined" ? "system" : readStoredPreference(),
  systemDark: typeof window === "undefined" ? false : systemPrefersDark(),
  setPreference: (preference) => {
    if (!isThemePreference(preference)) return;
    writeStoredPreference(preference);
    set({ preference });
  },
  setSystemDark: (systemDark) => set({ systemDark }),
  syncFromStorage: () => set({ preference: readStoredPreference() }),
}));

/** Test helper: re-reads storage and the OS scheme. */
export function resetThemeStore(): void {
  useThemeStore.setState({ preference: readStoredPreference(), systemDark: systemPrefersDark() });
}

export interface ThemeApi {
  preference: ThemePreference;
  resolvedTheme: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
}

export function useTheme(): ThemeApi {
  const preference = useThemeStore((s) => s.preference);
  const systemDark = useThemeStore((s) => s.systemDark);
  const setPreference = useThemeStore((s) => s.setPreference);
  return { preference, resolvedTheme: resolveTheme(preference, systemDark), setPreference };
}

/** "light" | "dark": for code that needs a JS value (Monaco themes, canvas colours). */
export function useResolvedTheme(): ResolvedTheme {
  return useThemeStore((s) => resolveTheme(s.preference, s.systemDark));
}

const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * Keeps <html> in sync with the store and the store in sync with the OS setting and
 * other tabs. Mount once, near the root (AppProviders).
 */
export function useThemeSync(): void {
  const preference = useThemeStore((s) => s.preference);
  const systemDark = useThemeStore((s) => s.systemDark);
  const resolved = resolveTheme(preference, systemDark);

  useIsomorphicLayoutEffect(() => {
    applyTheme(resolved, preference);
  }, [resolved, preference]);

  useEffect(() => {
    const { setSystemDark, syncFromStorage } = useThemeStore.getState();
    let media: MediaQueryList | null = null;
    try {
      media = typeof window.matchMedia === "function" ? window.matchMedia(DARK_SCHEME_QUERY) : null;
    } catch {
      media = null;
    }
    const onScheme = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media?.addEventListener?.("change", onScheme);
    if (media) setSystemDark(media.matches);
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === THEME_STORAGE_KEY) syncFromStorage();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      media?.removeEventListener?.("change", onScheme);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
}
