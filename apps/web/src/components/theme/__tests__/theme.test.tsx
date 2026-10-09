import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/components/theme/ThemeProvider";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import {
  applyTheme,
  readStoredPreference,
  resetThemeStore,
  resolveTheme,
  THEME_COLORS,
  THEME_STORAGE_KEY,
  useResolvedTheme,
  useThemeStore,
} from "@/lib/theme";

// --- a controllable `prefers-color-scheme: dark` media query ----------------------
type Listener = (event: MediaQueryListEvent) => void;
const media = { dark: false, listeners: new Set<Listener>() };
const originalMatchMedia = window.matchMedia;

function installMatchMedia() {
  window.matchMedia = ((query: string) => ({
    get matches() {
      return query.includes("prefers-color-scheme: dark") ? media.dark : false;
    },
    media: query,
    onchange: null,
    addEventListener: (_: string, listener: Listener) => media.listeners.add(listener),
    removeEventListener: (_: string, listener: Listener) => media.listeners.delete(listener),
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function setSystemDark(dark: boolean) {
  media.dark = dark;
  act(() => media.listeners.forEach((listener) => listener({ matches: dark } as MediaQueryListEvent)));
}

const root = document.documentElement;

function resetDocument() {
  root.removeAttribute("data-theme");
  root.removeAttribute("data-theme-preference");
  root.style.colorScheme = "";
  root.classList.remove("theme-switching");
  document.head.querySelector('meta[name="theme-color"]')?.remove();
}

beforeEach(() => {
  media.dark = false;
  media.listeners.clear();
  installMatchMedia();
  window.localStorage.clear();
  resetDocument();
  const meta = document.createElement("meta");
  meta.name = "theme-color";
  meta.content = THEME_COLORS.light;
  document.head.appendChild(meta);
  resetThemeStore();
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
  window.localStorage.clear();
  resetDocument();
  resetThemeStore();
});

describe("theme rules", () => {
  it("resolves system against the OS setting and keeps explicit choices", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
  });

  it("reads the stored preference, treating missing or unknown values as system", () => {
    expect(readStoredPreference()).toBe("system");
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    expect(readStoredPreference()).toBe("dark");
    window.localStorage.setItem(THEME_STORAGE_KEY, '"dark"');
    expect(readStoredPreference()).toBe("system");
  });

  it("falls back to system when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(readStoredPreference()).toBe("system");
  });

  it("writes the theme, preference, colour scheme and browser colour to the document", () => {
    applyTheme("dark", "system");
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(root).toHaveAttribute("data-theme-preference", "system");
    expect(root.style.colorScheme).toBe("dark");
    expect(document.head.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", THEME_COLORS.dark);
  });
});

function ResolvedProbe() {
  return <span data-testid="resolved">{useResolvedTheme()}</span>;
}

describe("ThemeProvider", () => {
  it("applies the saved choice on mount and persists a new one", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    resetThemeStore();
    render(
      <ThemeProvider>
        <ResolvedProbe />
      </ThemeProvider>,
    );
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");

    act(() => useThemeStore.getState().setPreference("light"));
    expect(root).toHaveAttribute("data-theme", "light");
    expect(root).toHaveAttribute("data-theme-preference", "light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("follows the OS setting live while the preference is system", () => {
    render(<ThemeProvider />);
    expect(root).toHaveAttribute("data-theme", "light");
    setSystemDark(true);
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(root).toHaveAttribute("data-theme-preference", "system");

    // An explicit choice wins over the OS setting.
    act(() => useThemeStore.getState().setPreference("light"));
    setSystemDark(true);
    expect(root).toHaveAttribute("data-theme", "light");
  });

  it("picks up a choice made in another tab", () => {
    render(<ThemeProvider />);
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "dark" }));
    });
    expect(root).toHaveAttribute("data-theme", "dark");
  });

  it("still switches when storage refuses the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    render(<ThemeProvider />);
    act(() => useThemeStore.getState().setPreference("dark"));
    expect(root).toHaveAttribute("data-theme", "dark");
  });
});

describe("ThemeToggle", () => {
  function renderToggle() {
    return render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
  }

  it("names the current theme and exposes it for tests", () => {
    renderToggle();
    const trigger = screen.getByTestId("theme-toggle");
    expect(trigger).toHaveAccessibleName("Theme: System (light)");
    expect(trigger).toHaveAttribute("data-theme-preference", "system");
    expect(trigger).toHaveAttribute("data-resolved-theme", "light");
  });

  it("offers light, dark and system as a radio choice and applies the pick", async () => {
    renderToggle();
    fireEvent.keyDown(screen.getByTestId("theme-toggle"), { key: "Enter" });

    const dark = await screen.findByTestId("theme-option-dark");
    expect(dark).toHaveAttribute("role", "menuitemradio");
    expect(screen.getByTestId("theme-option-system")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("theme-option-light")).toHaveAttribute("aria-checked", "false");

    await act(async () => {
      fireEvent.click(dark);
    });
    expect(root).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(screen.getByTestId("theme-toggle")).toHaveAccessibleName("Theme: Dark");
  });

  it("lets the keyboard reach every option", async () => {
    renderToggle();
    fireEvent.keyDown(screen.getByTestId("theme-toggle"), { key: "Enter" });
    const items = await screen.findAllByRole("menuitemradio");
    expect(items.map((item) => item.textContent)).toEqual(["Light", "Dark", "System"]);
  });
});
