import { Eye, EyeOff, KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { useDocumentTitle } from "@/lib/hooks";
import { ROLE_LABELS } from "@/lib/labels";

import { FieldShell } from "../admin/components/Field";
import { describeFailure, fieldErrorsOf } from "../admin/lib/serverErrors";
import { passwordHints } from "../admin/users/userRules";

function SecretInput({ value, onChange, autoComplete, ...control }: { value: string; onChange: (value: string) => void; autoComplete: string; id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string }) {
  const [visible, setVisible] = useState(false);
  return (
    <Input
      {...control}
      type={visible ? "text" : "password"}
      autoComplete={autoComplete}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      trailing={
        <button type="button" aria-label={visible ? "Hide password" : "Show password"} onClick={() => setVisible((v) => !v)} className="flex size-7 items-center justify-center rounded-sm hover:bg-surface-hover hover:text-fg">
          {visible ? <EyeOff /> : <Eye />}
        </button>
      }
    />
  );
}

/**
 * /app/account: who you are signed in as, and your own password (S13). While the account still has an
 * administrator-issued password this is the only page (RequireAuth sends every other route here).
 */
export default function AccountPage() {
  useDocumentTitle("Your account");
  const user = useAuthStore((s) => s.user);
  const organization = useAuthStore((s) => s.organization);
  const mustChange = useAuthStore((s) => s.mustChangePassword);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  if (!user) return null;

  const hints = next ? passwordHints(next, { email: user.email, fullName: user.full_name }) : [];
  const mismatch = confirmation.length > 0 && confirmation !== next;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    const local: Record<string, string> = {};
    if (!current) local.current_password = "Enter your current password.";
    if (!next) local.new_password = "Choose a new password.";
    else if (hints.length) local.new_password = `Needs: ${hints.join(" ")}`;
    if (next && confirmation !== next) local.confirmation = "The two new passwords don't match.";
    setErrors(local);
    if (Object.keys(local).length) return;
    setSaving(true);
    try {
      await api.post("/auth/password", { current_password: current, new_password: next });
      const wasForced = mustChange;
      setCurrent("");
      setNext("");
      setConfirmation("");
      toast.success("Password changed. You were signed out on your other devices.");
      await bootstrap({ force: true });
      if (wasForced) navigate("/app", { replace: true });
    } catch (error) {
      const fields = fieldErrorsOf(error);
      const server: Record<string, string> = {};
      if (fields.current_password) server.current_password = fields.current_password;
      const weak = fields.new_password ?? fields.password;
      if (weak) server.new_password = weak;
      setErrors(server);
      if (!Object.keys(server).length) setFormError(describeFailure(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <PageContainer width="narrow">
      <PageHeader title="Your account" description="How you sign in to Swayam." />
      <div className="grid gap-6">
        {mustChange ? (
          <Callout tone="warning" title="Choose your own password to continue" data-testid="password-change-required">
            Your administrator gave you a temporary password. Pick one only you know; then the rest of the app opens.
          </Callout>
        ) : null}

        <Card>
          <CardHeader title="Profile" />
          <CardContent className="grid gap-3 text-sm">
            <div className="grid gap-0.5">
              <span className="text-fg-muted">Name</span>
              <span className="font-medium text-fg">{user.full_name}</span>
            </div>
            <div className="grid gap-0.5">
              <span className="text-fg-muted">Email</span>
              <span className="font-medium text-fg">{user.email}</span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge size="sm" tone="neutral">
                {ROLE_LABELS[user.role]}
              </Badge>
              {organization ? (
                <Badge size="sm" tone="outline">
                  {organization.name}
                </Badge>
              ) : null}
            </div>
            <p className="text-[13px] text-fg-muted">Your name, email and role are managed by an administrator of your organisation.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader title="Change password" description="Use at least 12 characters. A few unrelated words make a strong password that is easy to remember." />
          <CardContent>
            <form className="grid gap-4" onSubmit={(event) => void submit(event)} noValidate data-testid="change-password-form">
              <FieldShell label="Current password" required error={errors.current_password}>
                {(control) => <SecretInput {...control} autoComplete="current-password" value={current} onChange={setCurrent} />}
              </FieldShell>
              <FieldShell label="New password" required error={errors.new_password} description={!errors.new_password && hints.length ? `Needs: ${hints.join(" ")}` : undefined}>
                {(control) => <SecretInput {...control} autoComplete="new-password" value={next} onChange={setNext} />}
              </FieldShell>
              <FieldShell label="Confirm new password" required error={errors.confirmation ?? (mismatch ? "The two new passwords don't match." : undefined)}>
                {(control) => <SecretInput {...control} autoComplete="new-password" value={confirmation} onChange={setConfirmation} />}
              </FieldShell>
              {formError ? <Callout tone="danger">{formError}</Callout> : null}
              <div>
                <Button type="submit" leftIcon={<KeyRound />} loading={saving} loadingText="Changing">
                  Change password
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
