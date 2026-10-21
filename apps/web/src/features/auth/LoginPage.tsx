import { Eye, EyeOff, Info } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";
import { z } from "zod";

import { Logo } from "@/components/brand/Logo";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Form, FormField, useZodForm } from "@/components/ui/Form";
import { Input } from "@/components/ui/Input";
import { getCsrfToken } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { formatClock } from "@/lib/format";
import { useDocumentTitle, useInterval } from "@/lib/hooks";
import { safeRedirectPath } from "@/lib/utils";

import { EvidenceTrace } from "./EvidenceTrace";
import { describeLoginError, type LoginErrorView } from "./loginErrors";

const loginSchema = z.object({
  email: z
    .string()
    .trim()
    .min(1, { error: "Enter your work email." })
    .max(320, { error: "That email address is too long." })
    .pipe(z.email({ error: "Enter a valid email address, like name@company.com." })),
  password: z.string().min(1, { error: "Enter your password." }).max(512, { error: "That password is too long." }),
});

const DEFAULT_DESTINATION = "/app/dashboard";

export default function LoginPage() {
  useDocumentTitle("Sign in");
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const status = useAuthStore((s) => s.status);
  const signedOutReason = useAuthStore((s) => s.signedOutReason);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const login = useAuthStore((s) => s.login);

  const [error, setError] = useState<LoginErrorView | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [blockedUntil, setBlockedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const destination = safeRedirectPath(params.get("next"), DEFAULT_DESTINATION, ["/app"]);

  // Returning visitors with a live session skip the form. No CSRF cookie means no session to resume.
  useEffect(() => {
    if (status === "idle" && getCsrfToken()) void bootstrap();
  }, [status, bootstrap]);

  const remaining = blockedUntil ? Math.max(0, Math.ceil((blockedUntil - now) / 1000)) : 0;
  useInterval(() => setNow(Date.now()), blockedUntil && remaining > 0 ? 1000 : null);
  useEffect(() => {
    if (blockedUntil && remaining === 0) setBlockedUntil(null);
  }, [blockedUntil, remaining]);

  // Validate on submit, then live: tabbing through an empty form should not raise errors.
  const form = useZodForm(loginSchema, { defaultValues: { email: "", password: "" }, mode: "onSubmit", reValidateMode: "onChange" });
  const submitting = form.formState.isSubmitting;

  if (status === "authenticated") return <Navigate to={destination} replace />;

  const onSubmit = async (values: z.output<typeof loginSchema>) => {
    setError(null);
    try {
      await login(values);
      navigate(destination, { replace: true });
    } catch (caught) {
      const view = describeLoginError(caught);
      setError(view);
      if (view.retryAfterSeconds) {
        setNow(Date.now());
        setBlockedUntil(Date.now() + view.retryAfterSeconds * 1000);
      }
      form.setValue("password", "");
      form.setFocus("password");
    }
  };

  const blocked = remaining > 0;

  return (
    <div className="flex min-h-dvh bg-surface">
      <div className="flex w-full flex-col lg:w-[46%] lg:min-w-[440px] xl:w-[42%]">
        <header className="flex items-center justify-between gap-4 px-6 pt-6 sm:px-10 sm:pt-8">
          <Logo />
          <ThemeToggle />
        </header>

        <main className="flex flex-1 items-center justify-center px-6 py-12 sm:px-10">
          <div className="w-full max-w-[380px]">
            <h1 className="text-title text-fg">Sign in</h1>
            <p className="mt-1.5 text-[15px] leading-6 text-fg-muted">Review interview evidence, manage roles and invite candidates.</p>

            <div className="mt-7 grid gap-3 empty:hidden" aria-live="polite">
              {!error && signedOutReason === "expired" ? (
                <Callout tone="info" title="Your session ended">
                  Sign in again to pick up where you left off.
                </Callout>
              ) : null}
              {error ? (
                <Callout tone={error.tone} title={error.title} data-testid="login-error">
                  {error.message}
                  {blocked ? <span className="mt-1 block font-medium tabular-nums text-fg">You can try again in {formatClock(remaining)}.</span> : null}
                  {error.requestId ? <span className="mt-1 block text-xs">Request ID {error.requestId}</span> : null}
                </Callout>
              ) : null}
            </div>

            <Form form={form} onSubmit={onSubmit} className="mt-7 space-y-5">
              <FormField name="email" label="Work email" required>
                {(field) => (
                  <Input
                    {...field}
                    value={String(field.value ?? "")}
                    type="email"
                    data-testid="login-email"
                    size="lg"
                    autoComplete="username"
                    inputMode="email"
                    autoCapitalize="none"
                    spellCheck={false}
                    autoFocus
                    placeholder="name@company.com"
                  />
                )}
              </FormField>

              <FormField name="password" label="Password" required>
                {(field) => (
                  <>
                    <Input
                      {...field}
                      value={String(field.value ?? "")}
                      type={showPassword ? "text" : "password"}
                      data-testid="login-password"
                      size="lg"
                      autoComplete="current-password"
                      onKeyDown={(event) => setCapsLock(event.getModifierState("CapsLock"))}
                      onKeyUp={(event) => setCapsLock(event.getModifierState("CapsLock"))}
                      trailing={
                        <button
                          type="button"
                          onClick={() => setShowPassword((v) => !v)}
                          aria-label={showPassword ? "Hide password" : "Show password"}
                          aria-pressed={showPassword}
                          className="flex size-8 items-center justify-center rounded-[7px] text-fg-subtle transition-colors hover:bg-surface-hover hover:text-fg"
                        >
                          {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                        </button>
                      }
                    />
                    {capsLock ? (
                      <p className="flex items-center gap-1.5 text-xs font-medium text-warning-fg">
                        <Info className="size-3.5" aria-hidden="true" />
                        Caps Lock is on
                      </p>
                    ) : null}
                  </>
                )}
              </FormField>

              <Button
                type="submit"
                data-testid="login-submit"
                size="lg"
                fullWidth
                loading={submitting}
                loadingText="Signing in…"
                disabled={blocked}
                className="mt-1"
              >
                {blocked ? `Try again in ${formatClock(remaining)}` : "Sign in"}
              </Button>
            </Form>

            <p className="mt-8 border-t border-line-subtle pt-5 text-[13px] leading-5 text-fg-muted">
              Taking an interview? Open the personal link in your invitation email. You don't need an account.
            </p>
          </div>
        </main>

        <footer className="px-6 pb-6 text-xs text-fg-subtle sm:px-10 sm:pb-8">Need access? Ask a workspace admin to invite you.</footer>
      </div>

      <aside className="theme-stage relative m-3 hidden flex-1 overflow-hidden rounded-[20px] bg-surface-sunken ring-1 ring-line ring-inset lg:flex" aria-label="About Swayam by TechSara">
        <div className="login-stage-glow pointer-events-none absolute inset-0" aria-hidden="true" />
        <div className="relative flex w-full flex-col items-center justify-center gap-10 px-10 py-12">
          <EvidenceTrace />
          <div className="max-w-[460px] text-left">
            <h2 className="text-[1.375rem] leading-8 font-semibold tracking-[-0.018em] text-fg">Every score traces back to what was said.</h2>
            <p className="mt-2 text-[15px] leading-6 text-fg-muted">
              The AI interviewer gathers evidence in a natural conversation. Your team reviews it and makes every hiring decision.
            </p>
          </div>
        </div>
      </aside>
    </div>
  );
}
