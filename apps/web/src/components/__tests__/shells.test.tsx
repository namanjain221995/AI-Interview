import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppProviders } from "@/app/providers";
import { CandidateShell } from "@/components/layout/CandidateShell";
import { StaffShell } from "@/components/layout/StaffShell";
import { DataTable } from "@/components/ui/DataTable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { permissionsForRole, resetAuthStore, useAuthStore } from "@/lib/auth";
import { resetThemeStore } from "@/lib/theme";

function renderAt(element: React.ReactElement) {
  const router = createMemoryRouter([{ path: "*", element }], { initialEntries: ["/app/dashboard"] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  resetThemeStore();
  act(() =>
    useAuthStore.getState().setSession({
      user: {
        id: "u1",
        organization_id: "o1",
        email: "sam@acme.test",
        full_name: "Sam Rivera",
        role: "ADMIN",
        is_active: true,
        last_login_at: null,
        created_at: "",
        updated_at: "",
      },
      organization: { id: "o1", name: "Acme Robotics", slug: "acme" },
      permissions: permissionsForRole("ADMIN"),
    }),
  );
});

afterEach(() => {
  // Unmount before resetting stores, so the reset doesn't re-render a live tree.
  cleanup();
  resetAuthStore();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("data-theme-preference");
});

describe("staff shell", () => {
  it("has a theme toggle in the top bar and applies the theme to the document", () => {
    renderAt(<StaffShell>page</StaffShell>);
    expect(screen.getByTestId("theme-toggle")).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });

  it("opens the navigation drawer from the menu button and closes it with Escape", async () => {
    renderAt(<StaffShell>page</StaffShell>);
    const toggle = screen.getByTestId("staff-nav-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const drawer = await screen.findByTestId("staff-nav-drawer");
    expect(drawer).toHaveAttribute("role", "dialog");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(drawer, { key: "Escape" });
    expect(screen.queryByTestId("staff-nav-drawer")).not.toBeInTheDocument();
  });
});

describe("candidate shell", () => {
  it("offers the theme toggle in its header", () => {
    renderAt(<CandidateShell orgName="Acme Robotics">step</CandidateShell>);
    expect(screen.getByTestId("theme-toggle")).toBeInTheDocument();
    expect(screen.getByText("Acme Robotics")).toBeInTheDocument();
  });

  it("shows the TechSara mark next to the organization name when it has no logo (never initials)", () => {
    const { container } = renderAt(<CandidateShell orgName="TechSara Solutions">step</CandidateShell>);
    const header = container.querySelector("header")!;
    expect(header.querySelector("[data-brand-mark]")).not.toBeNull();
    expect(header).toHaveTextContent("TechSara Solutions");
    expect(header).not.toHaveTextContent(/^TS/);
    expect(header.querySelector("[data-org-logo]")).toBeNull();
  });

  it("uses the organization's own logo when it has one", () => {
    const { container } = renderAt(<CandidateShell orgName="Acme Robotics" orgLogoUrl="/logos/acme.png">step</CandidateShell>);
    const header = container.querySelector("header")!;
    expect(header.querySelector("[data-org-logo]")?.getAttribute("src")).toBe("/logos/acme.png");
    expect(header.querySelector("[data-brand-mark]")).toBeNull();
  });
});

describe("tab bar", () => {
  it("keeps tabs keyboard reachable in a horizontally scrollable list", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList aria-label="Sections">
          <TabsTrigger value="a">Alpha</TabsTrigger>
          <TabsTrigger value="b">Beta</TabsTrigger>
        </TabsList>
        <TabsContent value="a">A</TabsContent>
        <TabsContent value="b">B</TabsContent>
      </Tabs>,
    );
    const list = screen.getByRole("tablist", { name: "Sections" });
    expect(list.className).toContain("overflow-x-auto");
    expect(list.className).toContain("overflow-y-hidden");
    expect(list.className).not.toContain("border-b");
    const alpha = screen.getByRole("tab", { name: "Alpha" });
    expect(alpha.className).not.toContain("-mb-px");
    expect(alpha).toHaveAttribute("aria-selected", "true");
  });
});

describe("data table on narrow screens", () => {
  it("pins the first column when asked", () => {
    render(
      <DataTable
        stickyFirstColumn
        columns={[
          { id: "name", header: "Name", cell: (r: { id: string; name: string }) => r.name },
          { id: "id", header: "Id", cell: (r: { id: string; name: string }) => r.id },
        ]}
        data={[{ id: "1", name: "Ada" }]}
        getRowId={(r) => r.id}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "Name" }).className).toContain("sticky");
    expect(screen.getByRole("cell", { name: "Ada" }).className).toContain("sticky");
    expect(screen.getByRole("cell", { name: "1" }).className).not.toContain("sticky");
  });
});
