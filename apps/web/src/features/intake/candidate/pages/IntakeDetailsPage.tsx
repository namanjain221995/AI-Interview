import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { StepHeader } from "@/features/candidate/components/StepLayout";
import { JourneyBoundary, JourneyError, useJourney, usePortalMe, type JourneyContext } from "@/features/candidate/hooks";
import { errorMessage, isApiError } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";

import { getPortalIntake, intakeKeys, intakeUnavailable, normalizePortalIntake, onboardingFieldErrors, saveOnboarding } from "../api";
import { OnboardingForm } from "../components/OnboardingForm";
import { draftFrom, formFieldFor, type OnboardingField } from "../onboarding";
import type { IntakeOnboardingIn } from "../types";

/** /candidate/:interviewId/details: the intake onboarding form (INTAKE_INTERVIEW.md §11.2). */
export default function IntakeDetailsPage() {
  useDocumentTitle("Your details");
  const { query, ctx } = useJourney("details");
  return (
    <div data-testid="intake-details-page">
      <StepHeader
        title="Your details"
        lede="A few questions about you and the role you're aiming for. They take about five minutes, and the interviewer confirms some answers with you instead of asking them again."
      />
      <div className="mt-10">
        <JourneyBoundary query={query}>{ctx ? <Details ctx={ctx} /> : null}</JourneyBoundary>
      </div>
    </div>
  );
}

function describeSaveError(error: unknown): string {
  if (!isApiError(error)) return errorMessage(error, "Check your connection and try again.");
  if (error.code === "onboarding_locked") return "Your interview has already started, so these details can't change now.";
  if (error.code === "eligibility_required") return "Answer the work eligibility questions, or say you'd rather discuss them with the hiring team.";
  if (error.status === 429) return "That was a lot of saves in a short time. Wait a minute, then try again.";
  if (error.isValidation) return "Some answers need attention. They're marked above.";
  return error.message || "Check your connection and try again.";
}

function Details({ ctx }: { ctx: JourneyContext }) {
  const queryClient = useQueryClient();
  const me = usePortalMe();
  const intake = useQuery({
    queryKey: intakeKeys.portal(ctx.interviewId),
    queryFn: () => getPortalIntake(ctx.interviewId),
    meta: { errorToast: false },
    refetchOnWindowFocus: false,
  });
  const [serverErrors, setServerErrors] = useState<Partial<Record<OnboardingField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: IntakeOnboardingIn) => saveOnboarding(ctx.interviewId, body),
    meta: { silentError: true },
    onMutate: () => setFormError(null),
    onSuccess: async (view) => {
      queryClient.setQueryData(intakeKeys.portal(ctx.interviewId), view);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portal.interview(ctx.interviewId), exact: true });
      ctx.next();
    },
    onError: (error) => {
      if (isApiError(error) && error.code === "consent_required") {
        ctx.goTo("consent", { replace: true });
        return;
      }
      if (isApiError(error) && error.code === "onboarding_locked") void intake.refetch();
      const mapped: Partial<Record<OnboardingField, string>> = {};
      if (isApiError(error)) {
        for (const [field, message] of Object.entries(onboardingFieldErrors(error))) {
          const key = formFieldFor(field);
          if (key && !mapped[key]) mapped[key] = message;
        }
      }
      setServerErrors(mapped);
      setFormError(Object.keys(mapped).length ? null : describeSaveError(error));
    },
  });

  if (intake.isPending || me.isPending) {
    return (
      <div className="grid gap-6" aria-busy="true" aria-label="Loading your details">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-11 w-full max-w-md" />
        <Skeleton className="h-6 w-56" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }
  // The intake routes may be off or not deployed yet: the form still works with defaults.
  if (intake.isError && !intakeUnavailable(intake.error)) return <JourneyError error={intake.error} onRetry={() => void intake.refetch()} />;

  const view = intake.data ?? normalizePortalIntake({}, ctx.interviewId);
  const initial = draftFrom(view.onboarding, me.data?.fullName ?? ctx.interview.candidateName, view.eligibility, view.targetRolePreset);
  // The form keeps its own draft; remount it only when the saved answers change identity.
  const formKey = `${view.onboardingSubmitted ? "saved" : "new"}:${view.onboardingLocked ? "locked" : "open"}`;

  return (
    <OnboardingForm
      key={formKey}
      initial={initial}
      eligibility={view.eligibility}
      email={me.data?.email ?? null}
      locked={view.onboardingLocked}
      submitting={save.isPending}
      serverErrors={serverErrors}
      formError={formError}
      onSubmit={(body) => save.mutate(body)}
      onBack={ctx.back}
      targetRolePreset={view.targetRolePreset}
      lockedAction={
        <Button size="lg" onClick={ctx.next} data-testid="intake-details-submit">
          Continue
        </Button>
      }
    />
  );
}
