import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { ApiError, api } from "@/lib/api";
import { permissionsForRole, resetAuthStore, useAuthStore } from "@/lib/auth";
import type { StaffRole } from "@/lib/types";

function signIn(role: StaffRole) {
  act(() =>
    useAuthStore.getState().setSession({
      user: {
        id: "u1",
        organization_id: "o1",
        email: "sam@acme.test",
        full_name: "Sam Rivera",
        role,
        is_active: true,
        last_login_at: null,
        created_at: "",
        updated_at: "",
      },
      organization: { id: "o1", name: "Acme Robotics", slug: "acme" },
      permissions: permissionsForRole(role),
    }),
  );
}

function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

beforeEach(() => {
  resetAuthStore();
  // No backend in unit tests: every query fails like an unreachable API.
  vi.spyOn(api, "get").mockRejectedValue(new ApiError({ status: 0, code: "network_error", message: "Can't reach the server." }));
});

afterEach(() => {
  cleanup();
  resetAuthStore();
});

describe("app routes", () => {
  it("sends anonymous visitors from / to the sign-in page", async () => {
    vi.spyOn(api, "get").mockRejectedValue(new ApiError({ status: 401, code: "unauthorized", message: "No session" }));
    const router = renderApp("/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });

  it("renders a lazy staff page inside the shell with role-filtered navigation", async () => {
    signIn("INTERVIEWER");
    renderApp("/app/jobs");
    expect(await screen.findByRole("heading", { level: 1, name: "Jobs" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: /sessions/i })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: /integrity review/i })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: /users/i })).not.toBeInTheDocument();
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
  });

  it("blocks pages the role cannot open", async () => {
    signIn("RECRUITER");
    renderApp("/app/users");
    expect(await screen.findByText("You don't have access to this page")).toBeInTheDocument();
  });

  it("shows the dashboard gracefully when the API is unavailable", async () => {
    signIn("ADMIN");
    renderApp("/app/dashboard");
    expect(await screen.findByRole("heading", { level: 1, name: /good (morning|afternoon|evening), sam/i })).toBeInTheDocument();
    expect(await screen.findByText("Couldn't load this list")).toBeInTheDocument();
    expect((await screen.findAllByText("Not available yet")).length).toBeGreaterThan(0);
  });

  it("renders not-found inside the shell and as a full page", async () => {
    signIn("ADMIN");
    renderApp("/app/does-not-exist");
    expect(await screen.findByText("This page doesn't exist")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    cleanup();
    renderApp("/totally/unknown");
    expect(await screen.findByText("404")).toBeInTheDocument();
  });

  it("derives the candidate stepper from the URL", async () => {
    renderApp("/candidate/abc123/consent");
    expect(await screen.findByRole("heading", { name: "Your privacy and consent" })).toBeInTheDocument();
    const steps = screen.getByRole("navigation", { name: "Interview steps" });
    const current = within(steps)
      .getAllByRole("listitem")
      .find((li) => li.getAttribute("aria-current") === "step");
    expect(current).toHaveTextContent("Consent");
  });
});
