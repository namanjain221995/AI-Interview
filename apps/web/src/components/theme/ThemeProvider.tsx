import type { ReactNode } from "react";

import { useThemeSync } from "@/lib/theme";

/**
 * Applies the app theme to <html> and follows OS / other-tab changes. Mounted once in
 * AppProviders. Read the theme with `useTheme()` / `useResolvedTheme()` from `@/lib/theme`.
 */
export function ThemeProvider({ children }: { children?: ReactNode }) {
  useThemeSync();
  return <>{children}</>;
}
