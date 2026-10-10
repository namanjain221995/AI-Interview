import { Eye, EyeOff, KeyRound, Mail, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { ROLE_LABELS } from "@/lib/labels";
import type { StaffRole } from "@/lib/types";

import { useCreateUser, useUpdateUser } from "../api/admin";
import { SecretField } from "../components/Controls";
import { FieldShell } from "../components/Field";
import { describeFailure, fieldErrorsOf } from "../lib/serverErrors";
import type { AdminUser } from "../types";
import { checkRoleChange, generatePassword, passwordHints, ROLE_DESCRIPTIONS, type Actor } from "./userRules";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function PasswordInput({ value, onChange, id, ...aria }: { value: string; onChange: (v: string) => void; id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string }) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="flex gap-2">
      <Input
        id={id}
        {...aria}
        type={visible ? "text" : "password"}
        autoComplete="new-password"
        className="font-mono"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        trailing={
          <button type="button" aria-label={visible ? "Hide password" : "Show password"} onClick={() => setVisible((v) => !v)} className="flex size-7 items-center justify-center rounded-sm hover:bg-surface-hover hover:text-fg">
            {visible ? <EyeOff /> : <Eye />}
          </button>
        }
        wrapperClassName="flex-1"
      />
      <Button
        variant="outline"
        leftIcon={<RefreshCw />}
        onClick={() => {
          onChange(generatePassword());
          setVisible(true);
        }}
      >
        Generate
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------- create

export function CreateUserDialog({ open, onOpenChange, roles }: { open: boolean; onOpenChange: (open: boolean) => void; roles: StaffRole[] }) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<StaffRole>("RECRUITER");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);
  const create = useCreateUser();

  useEffect(() => {
    if (open) {
      setFullName("");
      setEmail("");
      setRole(roles.includes("RECRUITER") ? "RECRUITER" : (roles[roles.length - 1] ?? "INTERVIEWER"));
      setPassword(generatePassword());
      setErrors({});
      setFormError(null);
      setCreated(null);
    }
  }, [open, roles]);

  const hints = passwordHints(password, { email, fullName });

  const submit = async () => {
    const found: Record<string, string> = {};
    if (!fullName.trim()) found.full_name = "Enter their name.";
    if (!EMAIL.test(email.trim())) found.email = "Enter a valid work email.";
    if (hints.length) found.password = `Password: ${hints.join(" ")}`;
    setErrors(found);
    setFormError(null);
    if (Object.keys(found).length) return;
    try {
      await create.mutateAsync({ full_name: fullName.trim(), email: email.trim().toLowerCase(), role, password });
      setCreated({ email: email.trim().toLowerCase(), password });
      toast.success(`${fullName.trim()} can now sign in`);
    } catch (error) {
      const fields = fieldErrorsOf(error);
      setErrors(fields);
      if (!Object.keys(fields).length) setFormError(describeFailure(error));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{created ? "User added" : "Add a team member"}</DialogTitle>
          <DialogDescription>
            {created ? "Share the temporary password privately. They can change it after signing in." : "They sign in with this email and a temporary password you share with them."}
          </DialogDescription>
        </DialogHeader>
        {created ? (
          <DialogBody className="grid gap-4">
            <Callout tone="warning" icon={<KeyRound />} title="This password is shown once">
              Copy it now and send it through a private channel.
            </Callout>
            <SecretField label="Email" value={created.email} />
            <SecretField label="Temporary password" value={created.password} />
          </DialogBody>
        ) : (
          <DialogBody className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <FieldShell label="Full name" required error={errors.full_name}>
                {(control) => <Input {...control} autoFocus value={fullName} onChange={(e) => setFullName(e.target.value)} />}
              </FieldShell>
              <FieldShell label="Work email" required error={errors.email}>
                {(control) => <Input {...control} type="email" leading={<Mail />} value={email} onChange={(e) => setEmail(e.target.value)} />}
              </FieldShell>
            </div>
            <FieldShell label="Role" required error={errors.role}>
              {() => (
                <RadioGroup value={role} onValueChange={(v) => setRole(v as StaffRole)} aria-label="Role" className="gap-2.5">
                  {roles.map((r) => (
                    <RadioGroupItem key={r} value={r} label={ROLE_LABELS[r]} description={ROLE_DESCRIPTIONS[r]} />
                  ))}
                </RadioGroup>
              )}
            </FieldShell>
            <FieldShell label="Temporary password" required error={errors.password} description={hints.length ? `Needs: ${hints.join(" ")}` : "Meets the password policy. They choose their own password when they first sign in."}>
              {(control) => <PasswordInput {...control} value={password} onChange={setPassword} />}
            </FieldShell>
            {formError ? <Callout tone="danger">{formError}</Callout> : null}
          </DialogBody>
        )}
        <DialogFooter>
          {created ? (
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button onClick={() => void submit()} loading={create.isPending} loadingText="Adding…">
                Add user
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------ change role

export function RoleDialog({ user, actor, roles, activeAdmins, onOpenChange }: { user: AdminUser | null; actor: Actor; roles: StaffRole[]; activeAdmins: number | null; onOpenChange: (open: boolean) => void }) {
  const [role, setRole] = useState<StaffRole>("RECRUITER");
  const [error, setError] = useState<string | null>(null);
  const update = useUpdateUser();
  useEffect(() => {
    if (user) {
      setRole(user.role);
      setError(null);
    }
  }, [user]);
  if (!user) return null;
  const check = checkRoleChange(actor, user, role, activeAdmins);

  const submit = async () => {
    if (!check.allowed || role === user.role) return;
    try {
      await update.mutateAsync({ id: user.id, input: { role } });
      toast.success(`${user.full_name} is now ${ROLE_LABELS[role].toLowerCase()}`);
      onOpenChange(false);
    } catch (e) {
      setError(describeFailure(e));
    }
  };

  return (
    <Dialog open={Boolean(user)} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Change {user.full_name}'s role</DialogTitle>
          <DialogDescription>Their access changes on their next request. Active sessions stay signed in.</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-4">
          <RadioGroup value={role} onValueChange={(v) => setRole(v as StaffRole)} aria-label="Role" className="gap-2.5">
            {roles.map((r) => (
              <RadioGroupItem key={r} value={r} label={ROLE_LABELS[r]} description={ROLE_DESCRIPTIONS[r]} />
            ))}
          </RadioGroup>
          {!check.allowed ? <Callout tone="warning">{check.reason}</Callout> : null}
          {error ? <Callout tone="danger">{error}</Callout> : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!check.allowed || role === user.role} loading={update.isPending}>
            Change role
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------- reset password

export function PasswordDialog({ user, onOpenChange }: { user: AdminUser | null; onOpenChange: (open: boolean) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const update = useUpdateUser();
  useEffect(() => {
    if (user) {
      setPassword(generatePassword());
      setError(null);
      setDone(false);
    }
  }, [user]);
  if (!user) return null;
  const hints = passwordHints(password, { email: user.email, fullName: user.full_name });

  const submit = async () => {
    if (hints.length) return;
    try {
      await update.mutateAsync({ id: user.id, input: { password } });
      setDone(true);
    } catch (e) {
      const fields = fieldErrorsOf(e);
      setError(fields.password ?? describeFailure(e));
    }
  };

  return (
    <Dialog open={Boolean(user)} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>{done ? "Password reset" : `Set a new password for ${user.full_name}`}</DialogTitle>
          <DialogDescription>{done ? "Share it privately; it's shown once. Their lockout, if any, is cleared, and they choose their own password when they next sign in." : "Use this when someone is locked out or forgot their password."}</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-4">
          {done ? (
            <SecretField label="Temporary password" value={password} />
          ) : (
            <FieldShell label="New password" error={error ?? undefined} description={hints.length ? `Needs: ${hints.join(" ")}` : "Meets the password policy."}>
              {(control) => <PasswordInput {...control} value={password} onChange={setPassword} />}
            </FieldShell>
          )}
        </DialogBody>
        <DialogFooter>
          {done ? (
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button onClick={() => void submit()} disabled={hints.length > 0} loading={update.isPending}>
                Set password
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
