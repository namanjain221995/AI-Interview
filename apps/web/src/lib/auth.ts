/**
 * Staff authentication state and RBAC gates (CONTRACTS §3).
 *
 * The session lives in HttpOnly cookies; this store only mirrors who the
 * server says we are (`GET /auth/me`). Permissions come from the server when
 * it sends them, otherwise from the role matrix below (a mirror of
 * app.core.rbac). UI gating is a convenience — the API enforces every check.
 */

import { createElement, Fragment, useEffect, type ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { create } from "zustand";

import { api, isApiError, onUnauthorized, type ApiError } from "@/lib/api";
import {
  PERMISSIONS,
  type LoginRequest,
  type MeResponse,
  type OrganizationRef,
  type Permission,
  type Role,
  type StaffRole,
  type StaffUser,
} from "@/lib/types";

// ------------------------------------------------------------- RBAC matrix

const STAFF_REVIEW: readonly Permission[] = [
  "jobs:read",
  "candidates:read",
  "sessions:read",
  "reports:read",
  "transcripts:read",
  "reports:release",
  "reviews:write",
  "analytics:read",
];

const RECRUITER: readonly Permission[] = [
  ...STAFF_REVIEW,
  "jobs:write",
  "templates:write",
  "question_bank:write",
  "candidates:write",
  "interviews:invite",
  "integrity:review",
];

const ADMIN: readonly Permission[] = [...RECRUITER, "users:manage", "settings:manage", "audit:read"];

export const ROLE_PERMISSIONS: Readonly<Record<Role, readonly Permission[]>> = {
  SUPER_ADMIN: [...ADMIN, "organizations:manage"],
  ADMIN,
  RECRUITER,
  INTERVIEWER: STAFF_REVIEW,
  CANDIDATE: ["portal:self"],
};

export const STAFF_ROLES: readonly StaffRole[] = ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"];

export function roleHasPermission(role: Role | null | undefined, permission: Permission): boolean {
  return role ? ROLE_PERMISSIONS[role].includes(permission) : false;
}

export function permissionsForRole(role: Role): Permission[] {
  return [...ROLE_PERMISSIONS[role]];
}

// ------------------------------------------------------------ normalising

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStaffRole(value: unknown): value is StaffRole {
  return typeof value === "string" && (STAFF_ROLES as readonly string[]).includes(value);
}

const KNOWN_PERMISSIONS = new Set<string>(PERMISSIONS);

/**
 * Accepts `{user, organization, permissions}` or a flat user object (with an
 * optional nested `organization`) and returns a MeResponse. Throws when the
 * payload does not describe an active staff user.
 */
export function normalizeMe(raw: unknown): MeResponse {
  if (!isRecord(raw)) throw new Error("auth: unexpected /auth/me payload");
  const userSource = isRecord(raw.user) ? raw.user : raw;
  const role = userSource.role;
  if (typeof userSource.id !== "string" || typeof userSource.email !== "string" || !isStaffRole(role)) {
    throw new Error("auth: payload is not a staff user");
  }
  const user: StaffUser = {
    id: userSource.id,
    email: userSource.email,
    full_name:
      typeof userSource.full_name === "string" && userSource.full_name
        ? userSource.full_name
        : typeof userSource.display_name === "string" && userSource.display_name
          ? userSource.display_name
          : userSource.email,
    role,
    organization_id: typeof userSource.organization_id === "string" ? userSource.organization_id : null,
    is_active: userSource.is_active !== false,
    last_login_at: typeof userSource.last_login_at === "string" ? userSource.last_login_at : null,
    created_at: typeof userSource.created_at === "string" ? userSource.created_at : "",
    updated_at: typeof userSource.updated_at === "string" ? userSource.updated_at : "",
  };
  const orgSource = isRecord(raw.organization) ? raw.organization : isRecord(userSource.organization) ? userSource.organization : null;
  const organization: OrganizationRef | null =
    orgSource && typeof orgSource.id === "string" && typeof orgSource.name === "string"
      ? { id: orgSource.id, name: orgSource.name, slug: typeof orgSource.slug === "string" ? orgSource.slug : "" }
      : null;
  const permissionSource = Array.isArray(raw.permissions) ? raw.permissions : Array.isArray(userSource.permissions) ? userSource.permissions : null;
  const permissions = permissionSource
    ? (permissionSource.filter((p): p is Permission => typeof p === "string" && KNOWN_PERMISSIONS.has(p)) as Permission[])
    : permissionsForRole(role);
  return { user, organization, permissions };
}

// ------------------------------------------------------------------- store

export type AuthStatus = "idle" | "loading" | "authenticated" | "anonymous" | "error";
export type SignedOutReason = "logout" | "expired" | null;

export interface AuthState {
  status: AuthStatus;
  user: StaffUser | null;
  organization: OrganizationRef | null;
  permissions: Permission[];
  /** Set when the session check failed for a reason other than "not signed in". */
  error: ApiError | null;
  signedOutReason: SignedOutReason;
  /** Resolves the current session once; pass force to re-check. */
  bootstrap: (options?: { force?: boolean }) => Promise<void>;
  login: (credentials: LoginRequest) => Promise<MeResponse>;
  logout: () => Promise<void>;
  setSession: (me: MeResponse) => void;
  clearSession: (reason?: SignedOutReason) => void;
}

const initialState = {
  status: "idle" as AuthStatus,
  user: null,
  organization: null,
  permissions: [] as Permission[],
  error: null,
  signedOutReason: null as SignedOutReason,
};

let bootstrapInFlight: Promise<void> | null = null;

export const useAuthStore = create<AuthState>()((set, get) => ({
  ...initialState,

  bootstrap: (options) => {
    const { status } = get();
    if (!options?.force && (status === "authenticated" || status === "anonymous")) return Promise.resolve();
    if (bootstrapInFlight) return bootstrapInFlight;
    set({ status: "loading", error: null });
    bootstrapInFlight = (async () => {
      try {
        const raw = await api.get<unknown>("/auth/me");
        get().setSession(normalizeMe(raw));
      } catch (error) {
        if (isApiError(error) && (error.status === 401 || error.status === 403)) {
          set({ ...initialState, status: "anonymous", signedOutReason: get().signedOutReason });
        } else if (isApiError(error)) {
          // API unreachable or failing: keep the visitor where they are and offer a retry.
          set({ status: "error", error });
        } else {
          // The payload did not describe a staff user (e.g. a candidate session): treat as signed out.
          set({ ...initialState, status: "anonymous" });
        }
      } finally {
        bootstrapInFlight = null;
      }
    })();
    return bootstrapInFlight;
  },

  login: async ({ email, password }) => {
    const raw = await api.post<unknown>(
      "/auth/login",
      { email: email.trim().toLowerCase(), password },
      { skipAuthRefresh: true },
    );
    let me: MeResponse;
    try {
      me = normalizeMe(raw);
    } catch {
      me = normalizeMe(await api.get<unknown>("/auth/me"));
    }
    get().setSession(me);
    return me;
  },

  logout: async () => {
    try {
      await api.post("/auth/logout", undefined, { skipAuthRefresh: true, timeoutMs: 8000 });
    } catch {
      // The local session is cleared regardless; cookies expire server-side.
    }
    get().clearSession("logout");
  },

  setSession: (me) =>
    set({
      status: "authenticated",
      user: me.user,
      organization: me.organization,
      permissions: me.permissions ?? permissionsForRole(me.user.role),
      error: null,
      signedOutReason: null,
    }),

  clearSession: (reason = null) => set({ ...initialState, status: "anonymous", signedOutReason: reason }),
}));

/** Test helper: restore the pristine store. */
export function resetAuthStore(): void {
  bootstrapInFlight = null;
  useAuthStore.setState({ ...initialState });
}

// A rejected refresh means the server ended our session.
onUnauthorized(() => {
  if (useAuthStore.getState().status === "authenticated") {
    useAuthStore.getState().clearSession("expired");
  }
});

// ------------------------------------------------------------------- hooks

export function useCurrentUser(): StaffUser | null {
  return useAuthStore((s) => s.user);
}

export function useOrganization(): OrganizationRef | null {
  return useAuthStore((s) => s.organization);
}

export function usePermissions(): Permission[] {
  return useAuthStore((s) => s.permissions);
}

export function hasPermission(permissions: readonly Permission[], permission: Permission): boolean {
  return permissions.includes(permission);
}

export function usePermission(permission: Permission): boolean {
  return useAuthStore((s) => s.permissions.includes(permission));
}

/** True when the user holds any (default) or all of the given permissions. */
export function useCan(required: readonly Permission[], mode: "any" | "all" = "any"): boolean {
  return useAuthStore((s) =>
    required.length === 0 ? true : mode === "all" ? required.every((p) => s.permissions.includes(p)) : required.some((p) => s.permissions.includes(p)),
  );
}

export function useHasRole(...roles: Role[]): boolean {
  return useAuthStore((s) => (s.user ? roles.includes(s.user.role) : false));
}

// -------------------------------------------------------------- components

export interface RequireAuthProps {
  children?: ReactNode;
  /** Shown while the session is being resolved. */
  loadingFallback?: ReactNode;
  /** Shown when the session check failed (e.g. API unreachable). */
  errorFallback?: (error: ApiError, retry: () => void) => ReactNode;
  /** Where anonymous visitors are sent (default /login). */
  loginPath?: string;
}

/**
 * Renders children (or the nested route Outlet) for signed-in staff only.
 * Anonymous visitors are redirected to the login page with `?next=` set to
 * the page they asked for.
 */
export function RequireAuth({ children, loadingFallback = null, errorFallback, loginPath = "/login" }: RequireAuthProps) {
  const status = useAuthStore((s) => s.status);
  const error = useAuthStore((s) => s.error);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const location = useLocation();

  useEffect(() => {
    if (status === "idle") void bootstrap();
  }, [status, bootstrap]);

  if (status === "authenticated") return createElement(Fragment, null, children ?? createElement(Outlet));
  if (status === "anonymous") {
    const next = `${location.pathname}${location.search}${location.hash}`;
    const to = next && next !== "/" ? `${loginPath}?next=${encodeURIComponent(next)}` : loginPath;
    return createElement(Navigate, { to, replace: true });
  }
  if (status === "error" && error && errorFallback) {
    return createElement(Fragment, null, errorFallback(error, () => void bootstrap({ force: true })));
  }
  return createElement(Fragment, null, loadingFallback);
}

export interface RequireRoleProps {
  roles: readonly Role[];
  children?: ReactNode;
  fallback?: ReactNode;
}

export function RequireRole({ roles, children, fallback = null }: RequireRoleProps) {
  const role = useAuthStore((s) => s.user?.role);
  const allowed = role ? roles.includes(role) : false;
  return createElement(Fragment, null, allowed ? (children ?? createElement(Outlet)) : fallback);
}

export interface RequirePermissionProps {
  /** A single permission, or several (see `mode`). */
  permission: Permission | readonly Permission[];
  mode?: "any" | "all";
  children?: ReactNode;
  fallback?: ReactNode;
}

export function RequirePermission({ permission, mode = "any", children, fallback = null }: RequirePermissionProps) {
  const required = typeof permission === "string" ? [permission] : permission;
  const allowed = useCan(required, mode);
  return createElement(Fragment, null, allowed ? (children ?? createElement(Outlet)) : fallback);
}
