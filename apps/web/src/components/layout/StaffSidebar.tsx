import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Link, useMatch, useResolvedPath } from "react-router";

import { Logo } from "@/components/brand/Logo";
import { Tooltip } from "@/components/ui/Tooltip";
import { hasFeature, useAuthStore, usePermissions } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { visibleNav, type StaffNavItem } from "./staffNav";

export interface StaffSidebarProps {
  collapsed: boolean;
  onToggleCollapsed?: () => void;
  /** Called after a navigation (closes the mobile drawer). */
  onNavigate?: () => void;
  className?: string;
}

/**
 * One navigation entry. Active state is computed here (not via NavLink's
 * function className) because Radix Slot, used by the collapsed-mode
 * tooltip, can only merge string class names.
 */
function SidebarLink({ item, collapsed, onNavigate }: { item: StaffNavItem; collapsed: boolean; onNavigate?: () => void }) {
  const resolved = useResolvedPath(item.to);
  const isActive = Boolean(useMatch({ path: resolved.pathname, end: false }));
  const Icon = item.icon;

  const link = (
    <Link
      to={item.to}
      onClick={onNavigate}
      aria-current={isActive ? "page" : undefined}
      aria-label={collapsed ? item.label : undefined}
      className={cn(
        "group/nav flex h-8 items-center gap-2.5 rounded-control text-[13.5px] font-medium transition-[background-color,color,box-shadow] duration-100",
        collapsed ? "justify-center px-0" : "px-2.5",
        isActive ? "bg-surface text-fg shadow-[0_1px_2px_rgb(16_24_40/0.06),0_0_0_1px_var(--border)]" : "text-fg-muted hover:bg-surface-hover hover:text-fg",
      )}
    >
      <Icon
        className={cn("size-4 shrink-0 transition-colors", isActive ? "text-accent" : "text-fg-subtle group-hover/nav:text-fg-muted")}
        strokeWidth={isActive ? 2.25 : 2}
        aria-hidden="true"
      />
      {!collapsed ? <span className="truncate">{item.label}</span> : null}
    </Link>
  );

  return collapsed ? (
    <Tooltip content={item.label} side="right">
      {link}
    </Tooltip>
  ) : (
    link
  );
}

export function StaffSidebar({ collapsed, onToggleCollapsed, onNavigate, className }: StaffSidebarProps) {
  const permissions = usePermissions();
  const user = useAuthStore((st) => st.user);
  const organization = useAuthStore((st) => st.organization);
  const groups = visibleNav(permissions, (feature) => hasFeature({ user, organization }, feature));

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className={cn("flex h-14 shrink-0 items-center", collapsed ? "justify-center px-2" : "px-4")}>
        <Link to="/app/dashboard" onClick={onNavigate} className="rounded-sm" aria-label="Interview Studio home">
          <Logo collapsed={collapsed} />
        </Link>
      </div>

      <nav aria-label="Main" className={cn("flex-1 overflow-y-auto pb-4 scrollbar-thin", collapsed ? "px-2" : "px-3")}>
        {groups.map((group, groupIndex) => (
          <div key={group.key} className={cn(groupIndex > 0 && (collapsed ? "mt-3 border-t border-line pt-3" : "mt-5"))}>
            {group.label && !collapsed ? <p className="mb-1 px-2.5 text-xs font-medium text-fg-subtle">{group.label}</p> : null}
            <ul className="grid gap-0.5">
              {group.items.map((item) => (
                <li key={item.key}>
                  <SidebarLink item={item} collapsed={collapsed} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {onToggleCollapsed ? (
        <div className={cn("shrink-0 border-t border-line py-2.5", collapsed ? "px-2" : "px-3")}>
          <Tooltip content={collapsed ? "Expand sidebar" : "Collapse sidebar"} side="right" disabled={!collapsed}>
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
              className={cn(
                "flex h-8 w-full items-center gap-2.5 rounded-control text-[13px] font-medium text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg",
                collapsed ? "justify-center" : "px-2.5",
              )}
            >
              {collapsed ? <PanelLeftOpen className="size-4" aria-hidden="true" /> : <PanelLeftClose className="size-4" aria-hidden="true" />}
              {!collapsed ? <span>Collapse</span> : null}
            </button>
          </Tooltip>
        </div>
      ) : null}
    </div>
  );
}
