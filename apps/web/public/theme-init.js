/*
 * Applies the saved colour theme before the first paint, so dark-theme users never
 * see a light flash. Loaded from <head> as a classic, render-blocking script because
 * the edge CSP (infra/caddy/Caddyfile) does not allow inline scripts.
 *
 * Contract shared with src/lib/theme.ts (keep both in sync, and keep this file
 * backward compatible: browsers may cache it between deploys):
 *   localStorage["app.theme"] = "light" | "dark" | "system"   (missing = "system")
 *   <html data-theme="light|dark" data-theme-preference="light|dark|system">
 */
(function () {
  var root = document.documentElement;
  var preference = "system";
  try {
    var stored = window.localStorage.getItem("app.theme");
    if (stored === "light" || stored === "dark" || stored === "system") preference = stored;
  } catch (error) {
    // Storage blocked (private mode, policy): follow the system setting.
  }
  var dark = preference === "dark";
  if (preference === "system") {
    try {
      dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (error) {
      dark = false;
    }
  }
  var theme = dark ? "dark" : "light";
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-theme-preference", preference);
  root.style.colorScheme = theme;
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#0a0e1c" : "#f5f7fb");
})();
