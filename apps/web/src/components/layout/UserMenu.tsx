import { ChevronDown, LogOut, Settings } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/DropdownMenu";
import { useAuthStore, usePermission } from "@/lib/auth";
import { ROLE_LABELS } from "@/lib/labels";

export function UserMenu() {
  const user = useAuthStore((s) => s.user);
  const organization = useAuthStore((s) => s.organization);
  const logout = useAuthStore((s) => s.logout);
  const canManageSettings = usePermission("settings:manage");
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  if (!user) return null;

  const signOut = async () => {
    setSigningOut(true);
    await logout();
    navigate("/login", { replace: true });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="flex h-9 items-center gap-2 rounded-control pr-1.5 pl-1 text-left transition-colors hover:bg-surface-hover data-[state=open]:bg-surface-hover"
        aria-label={`Account menu for ${user.full_name}`}
      >
        <Avatar name={user.full_name} size="md" />
        <span className="hidden min-w-0 flex-col md:flex">
          <span className="max-w-40 truncate text-[13px] leading-4 font-medium text-fg">{user.full_name}</span>
          <span className="max-w-40 truncate text-xs leading-4 text-fg-muted">{ROLE_LABELS[user.role]}</span>
        </span>
        <ChevronDown className="hidden size-4 text-fg-subtle md:block" aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <div className="flex items-center gap-3 px-2 py-2.5">
          <Avatar name={user.full_name} size="lg" />
          <div className="grid min-w-0 gap-0.5">
            <p className="truncate text-sm font-medium text-fg">{user.full_name}</p>
            <p className="truncate text-xs text-fg-muted">{user.email}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 px-2 pb-2">
          <Badge size="sm" tone="neutral">
            {ROLE_LABELS[user.role]}
          </Badge>
          {organization ? (
            <Badge size="sm" tone="outline">
              {organization.name}
            </Badge>
          ) : null}
        </div>
        {canManageSettings ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => navigate("/app/settings")}>
              <Settings />
              Workspace settings
            </DropdownMenuItem>
          </>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={(event) => {
            event.preventDefault();
            void signOut();
          }}
          disabled={signingOut}
        >
          <LogOut />
          {signingOut ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
