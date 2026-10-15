import { useEffect, type ReactNode } from "react";
import { Outlet, useLocation, useParams } from "react-router";
import { create } from "zustand";

import { BrandMark, PRODUCT_NAME } from "@/components/brand/Logo";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { Stepper, type StepperStep } from "@/components/ui/Stepper";
import { CANDIDATE_STEPS } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface CandidateShellProps {
  /** Hiring organization shown in the header. */
  orgName?: string | null;
  /** The organization's own logo; without one the header shows the TechSara mark. */
  orgLogoUrl?: string | null;
  /** Second header line, e.g. the job title. */
  subtitle?: ReactNode;
  /** Journey progress (usually a <Stepper/>). */
  stepper?: ReactNode;
  /** Right side of the header. */
  headerAside?: ReactNode;
  width?: "narrow" | "default" | "wide";
  children?: ReactNode;
}

const WIDTHS = { narrow: "max-w-xl", default: "max-w-3xl", wide: "max-w-5xl" } as const;

/** Calm, minimal frame for every candidate-facing page outside the interview room. */
export function CandidateShell({ orgName, orgLogoUrl, subtitle, stepper, headerAside, width = "default", children }: CandidateShellProps) {
  const name = orgName || null;
  const logo = orgLogoUrl || null;
  return (
    <div className="flex min-h-dvh flex-col bg-surface-muted text-[15px] leading-6">
      <a
        href="#candidate-main"
        className="sr-only z-[70] rounded-control bg-primary px-3 py-2 text-sm font-medium text-primary-fg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur-md supports-[backdrop-filter]:bg-surface/75">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-3 px-4 sm:h-16 sm:gap-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            {logo ? (
              <img src={logo} alt="" aria-hidden="true" data-org-logo="" className="size-9 shrink-0 rounded-[10px] object-contain" />
            ) : (
              <BrandMark size={34} />
            )}
            <div className="grid min-w-0">
              <p className="truncate text-sm leading-5 font-semibold text-fg">{name ?? PRODUCT_NAME}</p>
              {subtitle ? <p className="truncate text-[13px] leading-5 text-fg-muted">{subtitle}</p> : null}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {headerAside}
            <ThemeToggle />
          </div>
        </div>
      </header>

      {stepper ? (
        <div className="border-b border-line bg-surface">
          <div className="mx-auto max-w-5xl px-4 py-4 sm:px-6">{stepper}</div>
        </div>
      ) : null}

      <main id="candidate-main" tabIndex={-1} className="flex-1 outline-none">
        <div className={cn("mx-auto w-full min-w-0 px-4 py-8 sm:px-6 sm:py-12", WIDTHS[width])}>{children ?? <Outlet />}</div>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-5xl flex-col gap-3 px-4 py-6 text-[13px] leading-5 text-fg-muted sm:flex-row sm:items-start sm:justify-between sm:px-6">
          <p className="max-w-xl">
            This interview is run by an AI interviewer. {name ? `A person at ${name}` : "A person at the hiring organization"} reviews the results and makes
            every hiring decision. Recordings are deleted on the schedule shown on the consent step; your transcript stays with{" "}
            {name || "the hiring organization"} until they delete it, and you can ask them to.
          </p>
          <p className="flex shrink-0 items-center gap-2 text-fg-subtle">
            <BrandMark size={16} />
            {PRODUCT_NAME}
          </p>
        </div>
      </footer>
    </div>
  );
}

// ------------------------------------------------- route-level layout + chrome

export interface CandidateChrome {
  orgName?: string | null;
  orgLogoUrl?: string | null;
  subtitle?: string | null;
  /** Override the journey steps (e.g. drop "Round" when the round is fixed). */
  steps?: readonly StepperStep[];
  completed?: readonly string[];
  hideStepper?: boolean;
  width?: CandidateShellProps["width"];
}

const useChromeStore = create<{ chrome: CandidateChrome; setChrome: (chrome: CandidateChrome) => void }>()((set) => ({
  chrome: {},
  setChrome: (chrome) => set({ chrome }),
}));

/** Candidate pages describe the header and stepper they need; the layout renders them. */
export function useCandidateChrome(chrome: CandidateChrome): void {
  const setChrome = useChromeStore((s) => s.setChrome);
  const stepKeys = chrome.steps?.map((s) => s.key).join("|");
  const completedKeys = chrome.completed?.join("|");
  useEffect(() => {
    setChrome(chrome);
    // Re-run only when the described values change, not on every new object.
  }, [setChrome, chrome.orgName, chrome.orgLogoUrl, chrome.subtitle, chrome.hideStepper, chrome.width, stepKeys, completedKeys]);
}

const STEP_KEYS = new Set<string>(CANDIDATE_STEPS.map((s) => s.key));

/**
 * Layout route for /candidate/*: renders CandidateShell with the journey
 * stepper derived from the URL (/candidate/:interviewId/<step>).
 */
export function CandidateShellLayout() {
  const chrome = useChromeStore((s) => s.chrome);
  const { interviewId } = useParams();
  const location = useLocation();
  const segment = location.pathname.split("/").filter(Boolean)[2] ?? null;
  const current = segment && STEP_KEYS.has(segment) ? segment : null;
  const steps = chrome.steps ?? CANDIDATE_STEPS;
  const showStepper = Boolean(interviewId && current && !chrome.hideStepper);

  return (
    <CandidateShell
      orgName={chrome.orgName}
      orgLogoUrl={chrome.orgLogoUrl}
      subtitle={chrome.subtitle}
      width={chrome.width}
      stepper={
        showStepper ? (
          // More than seven steps (the intake journey) only fit side by side from lg up.
          <Stepper steps={steps} current={current} completed={chrome.completed} compactBelow={steps.length > 7 ? "lg" : "sm"} aria-label="Interview steps" />
        ) : undefined
      }
    >
      <Outlet />
    </CandidateShell>
  );
}
