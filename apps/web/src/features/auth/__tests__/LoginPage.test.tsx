import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/Tooltip";
import { ApiError, api } from "@/lib/api";
import { permissionsForRole, resetAuthStore, useAuthStore } from "@/lib/auth";
import type { MeResponse } from "@/lib/types";

import LoginPage from "../LoginPage";
import { describeLoginError } from "../loginErrors";

const me: MeResponse = {
  user: {
    id: "u1",
    organization_id: "o1",
    email: "priya@acme.test",
    full_name: "Priya Shah",
    role: "RECRUITER",
    is_active: true,
    last_login_at: null,
    created_at: "",
    updated_at: "",
  },
  organization: { id: "o1", name: "Acme", slug: "acme" },
  permissions: permissionsForRole("RECRUITER"),
};

function renderLogin(path = "/login") {
  const router = createMemoryRouter(
    [
      { path: "/login", element: <LoginPage /> },
      { path: "/app/*", element: <p>staff app</p> },
    ],
    { initialEntries: [path] },
  );
  render(
    <TooltipProvider>
      <RouterProvider router={router} />
    </TooltipProvider>,
  );
  return router;
}

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText(/work email/i), { target: { value: email } });
  fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: password } });
}

beforeEach(() => resetAuthStore());
// Unmount before resetting so no mounted gate reacts to the reset (and refetches) after the test.
afterEach(() => {
  cleanup();
  resetAuthStore();
});

describe("LoginPage", () => {
  it("validates before calling the API", async () => {
    const post = vi.spyOn(api, "post");
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your work email.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    fill("not-an-email", "x");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/Enter a valid email address/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("signs in and goes to the requested page", async () => {
    vi.spyOn(api, "post").mockResolvedValueOnce(me);
    const router = renderLogin("/login?next=%2Fapp%2Fjobs%3Fstatus%3Dopen");
    fill("priya@acme.test", "correct horse battery");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("staff app")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/app/jobs");
    expect(router.state.location.search).toBe("?status=open");
  });

  it("ignores off-site redirect targets", async () => {
    vi.spyOn(api, "post").mockResolvedValueOnce(me);
    const router = renderLogin("/login?next=https%3A%2F%2Fevil.example");
    fill("priya@acme.test", "pw");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("staff app");
    expect(router.state.location.pathname).toBe("/app/dashboard");
  });

  it("shows invalid-credential errors and clears the password", async () => {
    vi.spyOn(api, "post").mockRejectedValueOnce(new ApiError({ status: 401, code: "invalid_credentials", message: "Invalid email or password" }));
    renderLogin();
    fill("priya@acme.test", "wrong");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Email or password is incorrect")).toBeInTheDocument();
    expect(screen.getByLabelText(/^password/i)).toHaveValue("");
  });

  it("pauses the form while rate limited", async () => {
    vi.spyOn(api, "post").mockRejectedValueOnce(new ApiError({ status: 429, code: "rate_limited", message: "Slow down", retryAfterSeconds: 90 }));
    renderLogin();
    fill("priya@acme.test", "pw");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Too many attempts")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /try again in 1:(29|30)/i });
    expect(button).toBeDisabled();
  });

  it("explains an expired session", async () => {
    act(() => useAuthStore.getState().clearSession("expired"));
    renderLogin();
    expect(await screen.findByText("Your session ended")).toBeInTheDocument();
  });

  it("redirects visitors who are already signed in", async () => {
    act(() => useAuthStore.getState().setSession(me));
    const router = renderLogin();
    await waitFor(() => expect(router.state.location.pathname).toBe("/app/dashboard"));
  });

  it("toggles password visibility", () => {
    renderLogin();
    const password = screen.getByLabelText(/^password/i);
    expect(password).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
  });
});

describe("describeLoginError", () => {
  it("maps lockout with the remaining time", () => {
    const view = describeLoginError(new ApiError({ status: 423, code: "account_locked", message: "Locked", retryAfterSeconds: 840 }));
    expect(view.title).toBe("Account temporarily locked");
    expect(view.message).toContain("14 minutes");
    expect(view.retryAfterSeconds).toBe(840);
  });

  it("uses locked_until when Retry-After is absent", () => {
    const until = new Date(Date.now() + 5 * 60_000).toISOString();
    const view = describeLoginError(new ApiError({ status: 403, code: "account_locked", message: "Locked", details: { locked_until: until } }));
    expect(view.message).toMatch(/5 minutes/);
  });

  it("warns before the last attempt", () => {
    const view = describeLoginError(new ApiError({ status: 401, code: "invalid_credentials", message: "x", details: { remaining_attempts: 1 } }));
    expect(view.message).toContain("1 attempt left");
  });

  it("covers deactivated accounts, network and server failures", () => {
    expect(describeLoginError(new ApiError({ status: 403, code: "account_disabled", message: "x" })).title).toBe("Account deactivated");
    expect(describeLoginError(new ApiError({ status: 0, code: "network_error", message: "x" })).title).toBe("Can't reach Swayam");
    const server = describeLoginError(new ApiError({ status: 500, code: "internal_error", message: "x", requestId: "r-1" }));
    expect(server.title).toBe("Sign-in is unavailable right now");
    expect(server.requestId).toBe("r-1");
  });
});
