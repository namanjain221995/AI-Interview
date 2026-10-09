import { Building2, Menu } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Outlet, useLocation } from "react-router";

import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { IconButton } from "@/components/ui/IconButton";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/Sheet";
import { useAuthStore } from "@/lib/auth";
import { useIsDesktop, usePersistentState } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { CommandMenuTrigger } from "./CommandMenu";
import { StaffSidebar } from "./StaffSidebar";
import { UserMenu } from "./UserMenu";

export interface StaffShellProps {
  /** Replaces the default ⌘K search trigger in the top bar. */
  search?: ReactNode;
  /** Page content; defaults to the nested route Outlet. */
  children?: ReactNode;
}

function OrgChip() {
  const organization = useAuthStore((s) => s.organization);
  const role = useAuthStore((s) => s.user?.role);
  const name = organization?.name ?? (role === "SUPER_ADMIN" ? "All organizations" : "Your organization");
  return (
    <div className="flex min-w-0 items-center gap-2" title={name}>
      <span className="flex size-6 shrink-0 items-center justify-center rounded-[7px] border border-line bg-surface-subtle text-fg-muted">
        <Building2 className="size-3.5" aria-hidden="true" />
      </span>
      <span className="truncate text-[13px] font-medium text-fg">{name}</span>
    </div>
  );
}

/**
 * Staff application frame: a collapsible sidebar on the canvas and the page
 * on an inset sheet with the top bar (organization, search, theme, account).
 * Below `lg` the sidebar becomes a drawer (hamburger button; Radix Dialog gives
 * the focus trap, Esc to close and focus return to the button).
 */
export function StaffShell({ search, children }: StaffShellProps) {
  const [collapsed, setCollapsed] = usePersistentState("staff.sidebar.collapsed", false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const isDesktop = useIsDesktop();
  const mainRef = useRef<HTMLElement>(null);
  const navToggleRef = useRef<HTMLButtonElement>(null);
  const location = useLocation();

  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [location.pathname]);

  // The drawer only exists below lg: close it when the window grows past the breakpoint
  // (otherwise an orphaned modal overlay stays on top of the desktop layout).
  useEffect(() => {
    if (isDesktop) setMobileOpen(false);
  }, [isDesktop]);

  return (
    <div className="flex h-dvh w-full overflow-hidden bg-surface-muted">
      <a
        href="#main-content"
        className="sr-only z-[70] rounded-control bg-primary px-3 py-2 text-sm font-medium text-primary-fg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>

      <aside
        className={cn(
          "hidden shrink-0 transition-[width] duration-200 ease-out lg:block",
          collapsed ? "w-[60px]" : "w-[232px]",
        )}
        aria-label="Sidebar"
      >
        <StaffSidebar collapsed={collapsed} onToggleCollapsed={() => setCollapsed((c) => !c)} />
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent
          id="staff-nav-drawer"
          side="left"
          size="sm"
          className="w-[min(280px,85vw)] bg-surface-muted"
          aria-describedby={undefined}
          data-testid="staff-nav-drawer"
          // The hamburger lives outside the Sheet root, so return focus to it explicitly.
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            navToggleRef.current?.focus();
          }}
        >
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <StaffSidebar collapsed={false} onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col lg:py-2 lg:pr-2">
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-surface lg:rounded-xl lg:border lg:border-line lg:shadow-[0_1px_3px_rgb(16_24_40/0.05)]">
          <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line-subtle px-3 sm:gap-3 sm:px-4 lg:px-5">
            <IconButton
              ref={navToggleRef}
              label="Open navigation"
              icon={<Menu />}
              className="lg:hidden"
              onClick={() => setMobileOpen(true)}
              tooltip={false}
              aria-expanded={mobileOpen}
              aria-controls={mobileOpen ? "staff-nav-drawer" : undefined}
              data-testid="staff-nav-toggle"
            />
            <div className="hidden min-w-0 shrink-0 md:block md:max-w-56">
              <OrgChip />
            </div>
            <div className="hidden h-5 w-px bg-line md:block" aria-hidden="true" />
            <div className="flex min-w-0 flex-1 items-center">{search ?? <CommandMenuTrigger />}</div>
            <div className="flex shrink-0 items-center gap-1 sm:gap-1.5">
              <ThemeToggle />
              <UserMenu />
            </div>
          </header>
          <main ref={mainRef} id="main-content" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto outline-none scrollbar-thin">
            {children ?? <Outlet />}
          </main>
        </div>
      </div>
    </div>
  );
}

/** Standard page width and padding inside the staff shell. */
export function PageContainer({ children, className, width = "default" }: { children: ReactNode; className?: string; width?: "default" | "narrow" | "full" }) {
  return (
    <div
      className={cn(
        "mx-auto w-full px-4 py-6 sm:px-6 lg:px-8 lg:py-8",
        width === "default" && "max-w-[1240px]",
        width === "narrow" && "max-w-3xl",
        className,
      )}
    >
      {children}
    </div>
  );
}
