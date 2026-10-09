import type { ComponentType } from "react";
import { createBrowserRouter, Navigate, Outlet, ScrollRestoration, type RouteObject } from "react-router";

import { CandidateShellLayout } from "@/components/layout/CandidateShell";
import { AppSplash } from "@/components/layout/FullPageStates";

import { NavigationProgress } from "./NavigationProgress";
import { loadAvatarPlayground } from "./routes/AvatarPlaygroundRoute";
import { NotFoundPage, NotFoundPanel } from "./routes/NotFound";
import { RouteErrorBoundary } from "./routes/RouteErrorBoundary";
import { PermissionGate, StaffArea } from "./routes/StaffArea";

/** Code-split route: the page module's default export becomes the route component. */
function page(loader: () => Promise<{ default: ComponentType }>) {
  return async () => ({ Component: (await loader()).default });
}

function RootLayout() {
  return (
    <>
      <NavigationProgress />
      <Outlet />
      <ScrollRestoration />
    </>
  );
}

/**
 * The design-system showcase and the avatar playground are development tools. They
 * exist in `vite dev` and, opt-in, in builds made with VITE_ENABLE_DEV_ROUTES=true.
 * Keep this condition inline (no helper call): it is constant at build time, so the
 * production bundle drops the branch and never emits the dev chunks.
 */
export const DEV_ROUTES_ENABLED: boolean = import.meta.env.DEV || import.meta.env.VITE_ENABLE_DEV_ROUTES === "true";

const recruiter = {
  dashboard: page(() => import("@/features/recruiter/pages/DashboardPage")),
  jobs: page(() => import("@/features/recruiter/pages/JobsPage")),
  jobEditor: page(() => import("@/features/recruiter/pages/JobEditorPage")),
  jobDetail: page(() => import("@/features/recruiter/pages/JobDetailPage")),
  candidates: page(() => import("@/features/recruiter/pages/CandidatesPage")),
  candidateDetail: page(() => import("@/features/recruiter/pages/CandidateDetailPage")),
  sessions: page(() => import("@/features/recruiter/pages/SessionsPage")),
  sessionDetail: page(() => import("@/features/recruiter/pages/SessionDetailPage")),
  report: page(() => import("@/features/recruiter/pages/RecruiterReportPage")),
  integrity: page(() => import("@/features/recruiter/pages/IntegrityReviewPage")),
  questionBank: page(() => import("@/features/recruiter/pages/QuestionBankPage")),
  codingQuestions: page(() => import("@/features/recruiter/pages/CodingQuestionsPage")),
  templates: page(() => import("@/features/recruiter/pages/TemplatesPage")),
  analytics: page(() => import("@/features/recruiter/pages/AnalyticsPage")),
  settings: page(() => import("@/features/recruiter/pages/SettingsPage")),
  users: page(() => import("@/features/recruiter/pages/UsersPage")),
};

// Open interview links (docs/design/OPEN_LINKS.md): staff pages and the two public candidate pages.
const links = {
  list: page(() => import("@/features/links/pages/LinksPage")),
  editor: page(() => import("@/features/links/pages/LinkEditorPage")),
  detail: page(() => import("@/features/links/pages/LinkDetailPage")),
  register: page(() => import("@/features/links/pages/OpenLinkPage")),
  personal: page(() => import("@/features/links/pages/PersonalLinkPage")),
};

// Intake interviews, staff side (docs/design/INTAKE_INTERVIEW.md section 11.1).
const intake = {
  list: page(() => import("@/features/intake/staff/pages/IntakeListPage")),
  create: page(() => import("@/features/intake/staff/pages/IntakeCreatePage")),
  review: page(() => import("@/features/intake/staff/pages/IntakeReviewPage")),
  analytics: page(() => import("@/features/intake/staff/pages/IntakeAnalyticsPage")),
  settings: page(() => import("@/features/intake/staff/pages/IntakeSettingsPage")),
  bySession: page(() => import("@/features/intake/staff/pages/IntakeSessionRedirectPage")),
};

const candidate = {
  inviteEntry: page(() => import("@/features/candidate/pages/InviteEntryPage")),
  dashboard: page(() => import("@/features/candidate/pages/CandidateDashboardPage")),
  welcome: page(() => import("@/features/candidate/pages/WelcomePage")),
  instructions: page(() => import("@/features/candidate/pages/InstructionsPage")),
  consent: page(() => import("@/features/candidate/pages/ConsentPage")),
  resume: page(() => import("@/features/candidate/pages/ResumePage")),
  round: page(() => import("@/features/candidate/pages/RoundSelectPage")),
  systemCheck: page(() => import("@/features/candidate/pages/SystemCheckPage")),
  interview: page(() => import("@/features/candidate/pages/InterviewPage")),
  complete: page(() => import("@/features/candidate/pages/CompletePage")),
  report: page(() => import("@/features/candidate/pages/CandidateReportPage")),
  // Intake interviews only (docs/design/INTAKE_INTERVIEW.md section 11.2).
  intakeDetails: page(() => import("@/features/intake/candidate/pages/IntakeDetailsPage")),
  intakeResumeReview: page(() => import("@/features/intake/candidate/pages/IntakeResumeReviewPage")),
};

export const routes: RouteObject[] = [
  {
    id: "root",
    element: <RootLayout />,
    errorElement: <RouteErrorBoundary variant="page" />,
    hydrateFallbackElement: <AppSplash />,
    children: [
      { index: true, element: <Navigate to="/app/dashboard" replace /> },
      { path: "login", lazy: page(() => import("@/features/auth/LoginPage")) },

      // ------------------------------------------------------------ staff app
      {
        path: "app",
        element: <StaffArea />,
        errorElement: <RouteErrorBoundary variant="page" />,
        children: [
          {
            // Errors inside a page render in the shell, keeping navigation usable.
            errorElement: <RouteErrorBoundary variant="panel" />,
            children: [
              { index: true, element: <Navigate to="dashboard" replace /> },
              { path: "dashboard", lazy: recruiter.dashboard },
              { path: "jobs", lazy: recruiter.jobs },
              {
                element: <PermissionGate permission="jobs:write" />,
                children: [
                  { path: "jobs/new", lazy: recruiter.jobEditor },
                  { path: "jobs/:jobId/edit", lazy: recruiter.jobEditor },
                ],
              },
              { path: "jobs/:jobId", lazy: recruiter.jobDetail },
              { path: "candidates", lazy: recruiter.candidates },
              { path: "candidates/:candidateId", lazy: recruiter.candidateDetail },
              { path: "sessions", lazy: recruiter.sessions },
              { path: "sessions/:sessionId", lazy: recruiter.sessionDetail },
              // Reports are per session; the index lists completed sessions.
              { path: "reports", element: <Navigate to="/app/sessions?status=completed" replace /> },
              { path: "reports/:sessionId", lazy: recruiter.report },
              { element: <PermissionGate permission="integrity:review" />, children: [{ path: "integrity", lazy: recruiter.integrity }] },
              { path: "question-bank", lazy: recruiter.questionBank },
              { path: "coding-questions", lazy: recruiter.codingQuestions },
              { path: "templates", lazy: recruiter.templates },
              { path: "analytics", lazy: recruiter.analytics },
              { element: <PermissionGate permission="settings:manage" />, children: [{ path: "settings", lazy: recruiter.settings }] },
              { element: <PermissionGate permission="users:manage" />, children: [{ path: "users", lazy: recruiter.users }] },
              { path: "links", lazy: links.list },
              {
                element: <PermissionGate permission="interviews:invite" />,
                children: [
                  { path: "links/new", lazy: links.editor },
                  { path: "links/:linkId/edit", lazy: links.editor },
                ],
              },
              { path: "links/:linkId", lazy: links.detail },
              { path: "intake", lazy: intake.list },
              { element: <PermissionGate permission="interviews:invite" />, children: [{ path: "intake/new", lazy: intake.create }] },
              { element: <PermissionGate permission="analytics:read" />, children: [{ path: "intake/analytics", lazy: intake.analytics }] },
              { element: <PermissionGate permission="settings:manage" />, children: [{ path: "intake/settings", lazy: intake.settings }] },
              { path: "intake/session/:sessionId", lazy: intake.bySession },
              { path: "intake/:intakeId", lazy: intake.review },
              { path: "*", element: <NotFoundPanel /> },
            ],
          },
        ],
      },

      // -------------------------------------------------------- candidate app
      {
        path: "j/:token",
        element: <CandidateShellLayout />,
        errorElement: <RouteErrorBoundary variant="page" />,
        children: [{ index: true, lazy: links.register }],
      },
      {
        path: "r/:token",
        element: <CandidateShellLayout />,
        errorElement: <RouteErrorBoundary variant="page" />,
        children: [{ index: true, lazy: links.personal }],
      },
      {
        path: "i/:token",
        element: <CandidateShellLayout />,
        errorElement: <RouteErrorBoundary variant="page" />,
        children: [{ index: true, lazy: candidate.inviteEntry }],
      },
      {
        path: "candidate",
        errorElement: <RouteErrorBoundary variant="page" />,
        children: [
          // The interview room owns the full viewport (StageShell), outside the candidate frame.
          { path: ":interviewId/interview", lazy: candidate.interview },
          {
            element: <CandidateShellLayout />,
            children: [
              {
                errorElement: <RouteErrorBoundary variant="panel" />,
                children: [
                  { index: true, lazy: candidate.dashboard },
                  { path: ":interviewId", element: <Navigate to="welcome" replace /> },
                  { path: ":interviewId/welcome", lazy: candidate.welcome },
                  { path: ":interviewId/instructions", lazy: candidate.instructions },
                  { path: ":interviewId/consent", lazy: candidate.consent },
                  { path: ":interviewId/resume", lazy: candidate.resume },
                  { path: ":interviewId/details", lazy: candidate.intakeDetails },
                  { path: ":interviewId/resume-review", lazy: candidate.intakeResumeReview },
                  { path: ":interviewId/round", lazy: candidate.round },
                  { path: ":interviewId/system-check", lazy: candidate.systemCheck },
                  { path: ":interviewId/complete", lazy: candidate.complete },
                  { path: ":interviewId/report", lazy: candidate.report },
                  { path: "*", element: <NotFoundPanel homePath="/candidate" homeLabel="Go to your interviews" /> },
                ],
              },
            ],
          },
        ],
      },

      // ------------------------------------------------------------ dev tools
      ...(DEV_ROUTES_ENABLED
        ? [
            { path: "dev/ui", lazy: page(() => import("./dev/DesignSystemPage")) },
            { path: "dev/avatar", lazy: loadAvatarPlayground },
          ]
        : []),

      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
