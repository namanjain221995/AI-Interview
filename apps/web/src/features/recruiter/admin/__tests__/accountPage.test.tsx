import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/Tooltip";
import AccountPage from "@/features/recruiter/pages/AccountPage";
import { ApiError, api } from "@/lib/api";
import { permissionsForRole, resetAuthStore, useAuthStore } from "@/lib/auth";
import type { MeResponse } from "@/lib/types";

function me(mustChangePassword = false): MeResponse {
  return {
    user: {
      id: "u1",
      organization_id: "o1",
      email: "priya@acme.test",
      full_name: "Priya Shah",
      role: "RECRUITER",
      is_active: true,
      last_login_at: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    },
    organization: { id: "o1", name: "Acme", slug: "acme" },
    permissions: permissionsForRole("RECRUITER"),
    mustChangePassword,
  };
}

function renderPage() {
  const router = createMemoryRouter(
    [
      { path: "/app/account", element: <AccountPage /> },
      { path: "/app", element: <p>dashboard</p> },
    ],
    { initialEntries: ["/app/account"] },
  );
  render(
    <TooltipProvider>
      <RouterProvider router={router} />
    </TooltipProvider>,
  );
  return router;
}

function fill(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

beforeEach(() => resetAuthStore());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetAuthStore();
});

describe("AccountPage (S13)", () => {
  it("changes the password and opens the app once the temporary one is replaced", async () => {
    useAuthStore.getState().setSession(me(true));
    const post = vi.spyOn(api, "post").mockResolvedValueOnce(undefined);
    vi.spyOn(api, "get").mockResolvedValueOnce({ id: "u1", email: "priya@acme.test", role: "RECRUITER", display_name: "Priya Shah", must_change_password: false });
    const router = renderPage();
    expect(screen.getByTestId("password-change-required")).toBeInTheDocument();
    fill(/^Current password/, "temporary-Password-1");
    fill(/^New password/, "amber-Glacier-6612-river");
    fill(/^Confirm new password/, "amber-Glacier-6612-river");
    fireEvent.click(screen.getByRole("button", { name: /Change password/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/app"));
    expect(post).toHaveBeenCalledWith("/auth/password", { current_password: "temporary-Password-1", new_password: "amber-Glacier-6612-river" });
    expect(useAuthStore.getState().mustChangePassword).toBe(false);
  });

  it("checks the confirmation locally and shows the server's field errors", async () => {
    useAuthStore.getState().setSession(me());
    const post = vi.spyOn(api, "post");
    renderPage();
    expect(screen.queryByTestId("password-change-required")).not.toBeInTheDocument();
    fill(/^Current password/, "wrong-password-here");
    fill(/^New password/, "amber-Glacier-6612-river");
    fill(/^Confirm new password/, "something-else-entirely");
    fireEvent.click(screen.getByRole("button", { name: /Change password/ }));
    expect((await screen.findAllByText("The two new passwords don't match.")).length).toBeGreaterThan(0);
    expect(post).not.toHaveBeenCalled();

    post.mockRejectedValueOnce(
      new ApiError({ status: 422, code: "invalid_current_password", message: "The current password is incorrect.", details: { field: "current_password" } }),
    );
    fill(/^Confirm new password/, "amber-Glacier-6612-river");
    fireEvent.click(screen.getByRole("button", { name: /Change password/ }));
    expect(await screen.findByText(/The current password is incorrect/)).toBeInTheDocument();
  });
});
