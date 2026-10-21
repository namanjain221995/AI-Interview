import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router";

import { Logo } from "@/components/brand/Logo";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useDocumentTitle } from "@/lib/hooks";

import { ActionsSection } from "./sections/Actions";
import { DataDisplaySection } from "./sections/DataDisplay";
import { DomainSection } from "./sections/Domain";
import { FormsSection } from "./sections/Forms";
import { FoundationsSection } from "./sections/Foundations";
import { StageSection } from "./sections/Stage";

const NAV = [
  { id: "foundations", label: "Foundations" },
  { id: "actions", label: "Actions and overlays" },
  { id: "forms", label: "Form controls" },
  { id: "data", label: "Navigation and data" },
  { id: "domain", label: "Evidence and scoring" },
  { id: "stage", label: "Stage surface" },
];

/** /dev/ui — living reference for the design system. */
export default function DesignSystemPage() {
  useDocumentTitle("Design system");
  return (
    <div className="min-h-dvh bg-surface-muted">
      <header className="sticky top-0 z-30 border-b border-line bg-surface/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1280px] items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Logo />
            <span className="hidden h-5 w-px bg-line sm:block" aria-hidden="true" />
            <span className="hidden text-sm font-medium text-fg-muted sm:block">Design system</span>
            <Badge size="sm" tone="outline">
              v1
            </Badge>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <ThemeToggle />
            <Button variant="ghost" size="sm" className="max-sm:hidden" asChild>
              <Link to="/login">Sign-in page</Link>
            </Button>
            <Button variant="outline" size="sm" rightIcon={<ArrowUpRight />} asChild>
              <Link to="/app/dashboard">Open the app</Link>
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1280px] grid-cols-1 gap-10 px-4 py-10 sm:px-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <nav aria-label="Sections" className="hidden lg:block">
          <ul className="sticky top-24 grid gap-0.5">
            {NAV.map((item) => (
              <li key={item.id}>
                <a
                  href={`#${item.id}`}
                  className="flex h-8 items-center rounded-control px-2.5 text-[13px] font-medium text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg"
                >
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <main className="grid min-w-0 grid-cols-1 gap-12">
          <div className="max-w-2xl">
            <h1 className="text-display text-fg">Calm precision</h1>
            <p className="mt-3 text-[15px] leading-6 text-fg-muted">
              The components behind Swayam. Quiet chrome so the evidence stays loud: hairlines over heavy boxes, ink for the one primary action,
              and colour only where it carries meaning.
            </p>
          </div>
          <FoundationsSection />
          <ActionsSection />
          <FormsSection />
          <DataDisplaySection />
          <DomainSection />
          <StageSection />
        </main>
      </div>
    </div>
  );
}
