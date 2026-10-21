import { KeyRound, Lock, LockOpen, MoreHorizontal, ShieldCheck, UserCheck, UserCog, UserMinus, UserPlus } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { PageContainer } from "@/components/layout/StaffShell";
import { confirm } from "@/components/ui/AlertDialog";
import { Avatar } from "@/components/ui/Avatar";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Tooltip } from "@/components/ui/Tooltip";
import { useCurrentUser } from "@/lib/auth";
import { formatRelativeOrDate } from "@/lib/format";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { ROLE_LABELS } from "@/lib/labels";
import type { StaffRole } from "@/lib/types";

import { useActiveAdminCount, useDeactivateUser, useUnlockUser, useUpdateUser, useUsers } from "@/features/recruiter/admin/api/admin";
import { FilterSelect, SearchInput } from "@/features/recruiter/admin/components/Controls";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";
import type { AdminUser } from "@/features/recruiter/admin/types";
import { CreateUserDialog, PasswordDialog, RoleDialog } from "@/features/recruiter/admin/users/UserDialogs";
import { assignableRoles, canManageUser, checkDeactivate, checkReactivate, checkRoleChange, STAFF_ROLE_ORDER } from "@/features/recruiter/admin/users/userRules";

const PAGE_SIZE = 25;
const ROLE_TONE: Record<StaffRole, BadgeTone> = { SUPER_ADMIN: "accent", ADMIN: "accent", RECRUITER: "neutral", INTERVIEWER: "outline" };

/** /app/users (users:manage) */
export default function UsersPage() {
  useDocumentTitle("Users");
  const me = useCurrentUser();
  const actor = { id: me?.id ?? "", role: (me?.role ?? "INTERVIEWER") as StaffRole };
  const roles = assignableRoles(actor.role);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState<StaffRole | undefined>();
  const [state, setState] = useState<"active" | "inactive" | undefined>();
  const [page, setPage] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [roleTarget, setRoleTarget] = useState<AdminUser | null>(null);
  const [passwordTarget, setPasswordTarget] = useState<AdminUser | null>(null);
  const q = useDebounce(search.trim(), 250);
  const query = useMemo(() => ({ q: q || undefined, role, is_active: state === undefined ? undefined : state === "active", limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [q, role, state, page]);
  const users = useUsers(query);
  const adminCount = useActiveAdminCount();
  const activeAdmins = adminCount.data ?? null;
  const deactivate = useDeactivateUser();
  const update = useUpdateUser();
  const unlock = useUnlockUser();

  const onDeactivate = async (user: AdminUser) => {
    const ok = await confirm({
      title: `Deactivate ${user.full_name}?`,
      description: "They're signed out everywhere and can't sign in again until reactivated. Their reviews and history are kept.",
      confirmLabel: "Deactivate",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await deactivate.mutateAsync(user.id);
    } catch (error) {
      toast.error("Couldn't deactivate", { description: describeFailure(error) });
    }
  };

  const onReactivate = async (user: AdminUser) => {
    try {
      await update.mutateAsync({ id: user.id, input: { is_active: true } });
      toast.success(`${user.full_name} can sign in again`);
    } catch (error) {
      toast.error("Couldn't reactivate", { description: describeFailure(error) });
    }
  };

  const columns: DataTableColumn<AdminUser>[] = [
    {
      id: "name",
      header: "Person",
      cell: (u) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={u.full_name} />
          <div className="grid min-w-0">
            <span className="flex items-center gap-2 truncate font-medium text-fg">
              {u.full_name}
              {u.id === me?.id ? <Badge size="sm" tone="outline">You</Badge> : null}
            </span>
            <span className="truncate text-xs text-fg-muted">{u.email}</span>
          </div>
        </div>
      ),
    },
    {
      id: "role",
      header: "Role",
      width: "9rem",
      cell: (u) => (
        <Badge tone={ROLE_TONE[u.role]} size="sm" icon={u.role === "SUPER_ADMIN" || u.role === "ADMIN" ? <ShieldCheck /> : undefined}>
          {ROLE_LABELS[u.role]}
        </Badge>
      ),
    },
    {
      id: "status",
      header: "Status",
      width: "9rem",
      cell: (u) =>
        !u.is_active ? (
          <Badge tone="neutral" size="sm" dot>
            Deactivated
          </Badge>
        ) : u.is_locked ? (
          <Badge tone="warning" size="sm" icon={<Lock />}>
            Locked out
          </Badge>
        ) : (
          <Badge tone="success" size="sm" dot>
            Active
          </Badge>
        ),
    },
    { id: "last", header: "Last sign-in", hideBelow: "md", width: "9rem", cell: (u) => <span className="text-fg-muted">{u.last_login_at ? formatRelativeOrDate(u.last_login_at) : "Never"}</span> },
    { id: "added", header: "Added", hideBelow: "lg", width: "8rem", cell: (u) => <span className="text-fg-muted">{formatRelativeOrDate(u.created_at)}</span> },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      width: "3.5rem",
      cell: (u) => {
        const manageable = canManageUser(actor, u);
        const roleCheck = checkRoleChange(actor, u, null, activeAdmins);
        const deactivateCheck = checkDeactivate(actor, u, activeAdmins);
        const reactivateCheck = checkReactivate(actor, u);
        if (!manageable) {
          return (
            <Tooltip content="Only a super admin can manage a super admin.">
              <span tabIndex={0} className="inline-flex size-8 items-center justify-center text-fg-subtle">
                <Lock className="size-3.5" aria-label="Not editable" />
              </span>
            </Tooltip>
          );
        }
        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Actions for ${u.full_name}`} data-row-action>
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-60">
              <DropdownMenuItem disabled={!roleCheck.allowed} onSelect={() => setRoleTarget(u)} title={roleCheck.allowed ? undefined : roleCheck.reason}>
                <UserCog /> Change role
              </DropdownMenuItem>
              {!roleCheck.allowed ? <p className="px-2 pb-1.5 text-2xs text-fg-subtle">{roleCheck.reason}</p> : null}
              {u.id !== me?.id ? (
                <DropdownMenuItem onSelect={() => setPasswordTarget(u)}>
                  <KeyRound /> Set a new password
                </DropdownMenuItem>
              ) : null}
              {u.is_locked ? (
                <DropdownMenuItem onSelect={() => unlock.mutate(u.id)}>
                  <LockOpen /> Clear sign-in lockout
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuSeparator />
              {u.is_active ? (
                <>
                  <DropdownMenuItem destructive disabled={!deactivateCheck.allowed} onSelect={() => void onDeactivate(u)}>
                    <UserMinus /> Deactivate
                  </DropdownMenuItem>
                  {!deactivateCheck.allowed ? <p className="px-2 pb-1.5 text-2xs text-fg-subtle">{deactivateCheck.reason}</p> : null}
                </>
              ) : (
                <DropdownMenuItem disabled={!reactivateCheck.allowed} onSelect={() => void onReactivate(u)}>
                  <UserCheck /> Reactivate
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
    },
  ];

  const roleOptions = STAFF_ROLE_ORDER.filter((r) => actor.role === "SUPER_ADMIN" || r !== "SUPER_ADMIN").map((r) => ({ value: r, label: ROLE_LABELS[r] }));

  return (
    <PageContainer>
      <PageHeader
        title="Users"
        description="People on your team who can sign in to Swayam, and what they can do."
        actions={
          roles.length ? (
            <Button leftIcon={<UserPlus />} onClick={() => setCreateOpen(true)}>
              Add user
            </Button>
          ) : null
        }
      />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3">
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(0); }} placeholder="Search name or email" className="w-full sm:w-64" />
          <FilterSelect<StaffRole> label="Role" value={role} onChange={(v) => { setRole(v); setPage(0); }} options={roleOptions} allLabel="Any role" />
          <FilterSelect<"active" | "inactive">
            label="Status"
            value={state}
            onChange={(v) => {
              setState(v);
              setPage(0);
            }}
            options={[
              { value: "active", label: "Active" },
              { value: "inactive", label: "Deactivated" },
            ]}
            allLabel="Any status"
          />
          {activeAdmins !== null ? (
            <span className="ml-auto text-xs text-fg-muted max-sm:hidden">
              {activeAdmins} active {activeAdmins === 1 ? "administrator" : "administrators"}
            </span>
          ) : null}
        </div>
        <DataTable
          columns={columns}
          data={users.data?.items}
          getRowId={(u) => u.id}
          loading={users.isLoading}
          fetching={users.isFetching && !users.isLoading}
          error={users.error}
          onRetry={() => void users.refetch()}
          pageSize={PAGE_SIZE}
          page={page}
          total={users.data?.total ?? 0}
          onPageChange={setPage}
          caption="Users"
          empty={<EmptyState size="sm" icon={<UserCog />} title="No users match" description="Try another name or clear the filters." />}
        />
      </Card>
      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} roles={roles} />
      <RoleDialog user={roleTarget} actor={actor} roles={roles} activeAdmins={activeAdmins} onOpenChange={(open) => !open && setRoleTarget(null)} />
      <PasswordDialog user={passwordTarget} onOpenChange={(open) => !open && setPasswordTarget(null)} />
    </PageContainer>
  );
}
