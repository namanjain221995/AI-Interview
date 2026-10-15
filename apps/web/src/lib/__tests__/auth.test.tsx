import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import {
  normalizeMe,
  permissionsForRole,
  RequireAuth,
  RequirePermission,
  RequireRole,
  resetAuthStore,
  ROLE_PERMISSIONS,
  roleHasPermission,
  useAuthStore,
} from "@/lib/auth";
import type { MeResponse, Permission, StaffRole } from "@/lib/types";

function me(role: StaffRole = "RECRUITER", extra: Partial<MeResponse> = {}): MeResponse {
  return {
    user: {
      id: "u1",
      organization_id: "o1",
      email: "priya@acme.test",
      full_name: "Priya Shah",
      role,
      is_active: true,
      last_login_at: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    },
    organization: { id: "o1", name: "Acme", slug: "acme" },
    permissions: permissionsForRole(role),
    ...extra,
  };
}

beforeEach(() => resetAuthStore());
// Unmount before resetting so no mounted gate reacts to the reset (and refetches) after the test.
afterEach(() => {
  cleanup();
  resetAuthStore();
});

describe("role -> permission matrix (CONTRACTS §3)", () => {
  const expectations: Array<[Permission, StaffRole[]]> = [
    ["organizations:manage", ["SUPER_ADMIN"]],
    ["users:manage", ["SUPER_ADMIN", "ADMIN"]],
    ["settings:manage", ["SUPER_ADMIN", "ADMIN"]],
    ["audit:read", ["SUPER_ADMIN", "ADMIN"]],
    ["jobs:write", ["SUPER_ADMIN", "ADMIN", "RECRUITER"]],
    ["templates:write", ["SUPER_ADMIN", "ADMIN", "RECRUITER"]],
    ["question_bank:write", ["SUPER_ADMIN", "ADMIN", "RECRUITER"]],
    ["candidates:write", ["SUPER_ADMIN", "ADMIN", "RECRUITER"]],
    ["interviews:invite", ["SUPER_ADMIN", "ADMIN", "RECRUITER"]],
    ["integrity:review", ["SUPER_ADMIN", "ADMIN", "RECRUITER"]],
    ["jobs:read", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
    ["candidates:read", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
    ["sessions:read", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
    ["reports:read", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
    ["transcripts:read", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
    ["reports:release", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
    ["reviews:write", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
    ["analytics:read", ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"]],
  ];
  const staff: StaffRole[] = ["SUPER_ADMIN", "ADMIN", "RECRUITER", "INTERVIEWER"];

  it.each(expectations)("%s", (permission, allowed) => {
    for (const role of staff) expect(roleHasPermission(role, permission)).toBe(allowed.includes(role));
    expect(roleHasPermission("CANDIDATE", permission)).toBe(false);
  });

  it("gives candidates only portal access", () => {
    expect(ROLE_PERMISSIONS.CANDIDATE).toEqual(["portal:self"]);
    for (const role of staff) expect(roleHasPermission(role, "portal:self")).toBe(false);
  });
});

describe("normalizeMe", () => {
  it("accepts the nested shape and derives permissions from the role", () => {
    const raw = { user: me("INTERVIEWER").user, organization: { id: "o1", name: "Acme", slug: "acme" } };
    const result = normalizeMe(raw);
    expect(result.user.role).toBe("INTERVIEWER");
    expect(result.permissions).toEqual(permissionsForRole("INTERVIEWER"));
    expect(result.organization?.name).toBe("Acme");
  });

  it("accepts a flat user with server-provided permissions (unknown ones dropped)", () => {
    const result = normalizeMe({ ...me("ADMIN").user, organization: { id: "o2", name: "Globex" }, permissions: ["jobs:read", "made:up"] });
    expect(result.permissions).toEqual(["jobs:read"]);
    expect(result.organization).toEqual({ id: "o2", name: "Globex", slug: "" });
  });

  it("reads the must-change-password flag from the server", () => {
    const flat = { id: "u1", email: "a@b.test", role: "RECRUITER", display_name: "A", must_change_password: true, permissions: [] };
    expect(normalizeMe(flat).mustChangePassword).toBe(true);
    expect(normalizeMe({ ...flat, must_change_password: false }).mustChangePassword).toBe(false);
  });

  it("rejects candidates and malformed payloads", () => {
    expect(() => normalizeMe({ id: "c1", email: "c@x.co", role: "CANDIDATE" })).toThrow();
    expect(() => normalizeMe(null)).toThrow();
    expect(() => normalizeMe({ user: { id: 1 } })).toThrow();
  });
});

describe("auth store", () => {
  it("bootstraps an existing session", async () => {
    const spy = vi.spyOn(api, "get").mockResolvedValueOnce(me("ADMIN"));
    await useAuthStore.getState().bootstrap();
    const state = useAuthStore.getState();
    expect(spy).toHaveBeenCalledWith("/auth/me");
    expect(state.status).toBe("authenticated");
    expect(state.user?.full_name).toBe("Priya Shah");
    expect(state.permissions).toContain("users:manage");
  });

  it("becomes anonymous on 401 and records network failures as errors", async () => {
    vi.spyOn(api, "get").mockRejectedValueOnce(new ApiError({ status: 401, code: "unauthorized", message: "No session" }));
    await useAuthStore.getState().bootstrap();
    expect(useAuthStore.getState().status).toBe("anonymous");

    resetAuthStore();
    vi.spyOn(api, "get").mockRejectedValueOnce(new ApiError({ status: 0, code: "network_error", message: "Offline" }));
    await useAuthStore.getState().bootstrap();
    expect(useAuthStore.getState().status).toBe("error");
    expect(useAuthStore.getState().error?.code).toBe("network_error");
  });

  it("deduplicates concurrent bootstraps", async () => {
    const spy = vi.spyOn(api, "get").mockResolvedValue(me());
    await Promise.all([useAuthStore.getState().bootstrap(), useAuthStore.getState().bootstrap()]);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("logs in with a normalised email and skips refresh on bad credentials", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValueOnce(me("RECRUITER"));
    await useAuthStore.getState().login({ email: "  Priya@Acme.TEST ", password: "s3cret-password" });
    expect(post).toHaveBeenCalledWith("/auth/login", { email: "priya@acme.test", password: "s3cret-password" }, { skipAuthRefresh: true });
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("falls back to /auth/me when the login response has no user", async () => {
    vi.spyOn(api, "post").mockResolvedValueOnce({ ok: true });
    vi.spyOn(api, "get").mockResolvedValueOnce(me("INTERVIEWER"));
    const result = await useAuthStore.getState().login({ email: "i@acme.test", password: "pw" });
    expect(result.user.role).toBe("INTERVIEWER");
  });

  it("clears the session on logout even if the request fails", async () => {
    useAuthStore.getState().setSession(me());
    vi.spyOn(api, "post").mockRejectedValueOnce(new ApiError({ status: 0, code: "network_error", message: "Offline" }));
    await useAuthStore.getState().logout();
    const state = useAuthStore.getState();
    expect(state.status).toBe("anonymous");
    expect(state.user).toBeNull();
    expect(state.permissions).toEqual([]);
    expect(state.signedOutReason).toBe("logout");
  });
});

describe("gates", () => {
  function renderAt(path: string) {
    const router = createMemoryRouter(
      [
        { path: "/login", element: <p>login page</p> },
        {
          path: "/app",
          element: <RequireAuth loadingFallback={<p>checking session</p>} />,
          children: [
            { path: "jobs", element: <p>jobs page</p> },
            { path: "account", element: <p>account page</p> },
            {
              path: "users",
              element: <RequirePermission permission="users:manage" fallback={<p>forbidden</p>} />,
              children: [{ index: true, element: <p>users page</p> }],
            },
            {
              path: "admin-only",
              element: (
                <RequireRole roles={["ADMIN", "SUPER_ADMIN"]} fallback={<p>admins only</p>}>
                  <p>admin area</p>
                </RequireRole>
              ),
            },
          ],
        },
      ],
      { initialEntries: [path] },
    );
    render(<RouterProvider router={router} />);
    return router;
  }

  it("redirects anonymous visitors to /login with next", async () => {
    vi.spyOn(api, "get").mockRejectedValueOnce(new ApiError({ status: 401, code: "unauthorized", message: "No session" }));
    const router = renderAt("/app/jobs?status=open");
    expect(await screen.findByText("login page")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(new URLSearchParams(router.state.location.search).get("next")).toBe("/app/jobs?status=open");
  });

  it("renders protected content for signed-in staff", async () => {
    vi.spyOn(api, "get").mockResolvedValueOnce(me("RECRUITER"));
    renderAt("/app/jobs");
    expect(screen.getByText("checking session")).toBeInTheDocument();
    expect(await screen.findByText("jobs page")).toBeInTheDocument();
  });

  it("enforces permissions and roles", async () => {
    act(() => useAuthStore.getState().setSession(me("RECRUITER")));
    renderAt("/app/users");
    expect(await screen.findByText("forbidden")).toBeInTheDocument();
    expect(screen.queryByText("users page")).not.toBeInTheDocument();
  });

  it("keeps an account with an administrator-issued password on the account page (S13)", async () => {
    act(() => useAuthStore.getState().setSession(me("RECRUITER", { mustChangePassword: true })));
    const router = renderAt("/app/jobs");
    expect(await screen.findByText("account page")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/account");
    act(() => useAuthStore.getState().setSession(me("RECRUITER")));
    await act(() => router.navigate("/app/jobs"));
    expect(await screen.findByText("jobs page")).toBeInTheDocument();
  });

  it("a refused API call (password_change_required) sends the user to the account page", async () => {
    act(() => useAuthStore.getState().setSession(me("RECRUITER")));
    const router = renderAt("/app/jobs");
    expect(await screen.findByText("jobs page")).toBeInTheDocument();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: "password_change_required", message: "Choose a new password" } }), {
        status: 403,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(api.get("/jobs")).rejects.toMatchObject({ code: "password_change_required" });
    fetchSpy.mockRestore();
    expect(await screen.findByText("account page")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/account");
  });

  it("lets admins through", async () => {
    act(() => useAuthStore.getState().setSession(me("ADMIN")));
    renderAt("/app/users");
    expect(await screen.findByText("users page")).toBeInTheDocument();
  });

  it("switches to the role fallback for other roles", async () => {
    act(() => useAuthStore.getState().setSession(me("INTERVIEWER")));
    renderAt("/app/admin-only");
    expect(await screen.findByText("admins only")).toBeInTheDocument();
  });

  it("drops to the login page when the session expires mid-visit", async () => {
    act(() => useAuthStore.getState().setSession(me("RECRUITER")));
    const router = renderAt("/app/jobs");
    expect(await screen.findByText("jobs page")).toBeInTheDocument();
    act(() => useAuthStore.getState().clearSession("expired"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(useAuthStore.getState().signedOutReason).toBe("expired");
  });
});
