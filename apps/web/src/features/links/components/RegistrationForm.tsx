import { ChevronDown, LockKeyhole } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { inputVariants } from "@/components/ui/Input";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/utils";

import { initialValues, isoDay, toAnswers, validateValues, DATE_MAX_DAYS, type FormValues } from "../formSchema";
import type { DataUse, PublicFormField, RegisterRequest } from "../types";
import { DataUsePanel } from "./DataUsePanel";

export interface RegistrationFormProps {
  fields: readonly PublicFormField[];
  dataUse: DataUse | null;
  organizationName: string;
  requiresAccessCode: boolean;
  /** "preview" renders the same form for the link editor; it never submits. */
  mode?: "live" | "preview";
  submitting?: boolean;
  /** Field errors from the server (`details.fields`), keyed by field key or `access_code`. */
  serverErrors?: Record<string, string>;
  /** Error panel shown above the submit button. */
  formError?: ReactNode;
  onSubmit?: (body: RegisterRequest) => void;
  submitLabel?: string;
}

const AUTOCOMPLETE: Record<string, string> = {
  full_name: "name",
  email: "email",
  phone: "tel",
  location: "address-level2",
  current_title: "organization-title",
  current_company: "organization",
  linkedin_url: "url",
  portfolio_url: "url",
};

function nativeType(type: PublicFormField["type"]): string {
  switch (type) {
    case "email":
      return "email";
    case "phone":
      return "tel";
    case "url":
      return "url";
    case "number":
      return "number";
    case "date":
      return "date";
    default:
      return "text";
  }
}

function inputMode(type: PublicFormField["type"]): "email" | "tel" | "url" | "decimal" | "text" {
  if (type === "email") return "email";
  if (type === "phone") return "tel";
  if (type === "url") return "url";
  if (type === "number") return "decimal";
  return "text";
}

/**
 * The candidate's registration form (fields from the link's schema, the data-use
 * panel with its acknowledgement, optional access code, and a hidden honeypot).
 * The link editor renders the same component in preview mode.
 */
export function RegistrationForm({
  fields,
  dataUse,
  organizationName,
  requiresAccessCode,
  mode = "live",
  submitting = false,
  serverErrors,
  formError,
  onSubmit,
  submitLabel = "Register and continue",
}: RegistrationFormProps) {
  const preview = mode === "preview";
  const uid = useId();
  const fieldKeys = fields.map((f) => `${f.key}:${f.type}`).join("|");
  const [values, setValues] = useState<FormValues>(() => initialValues(fields));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [privacyAck, setPrivacyAck] = useState(false);
  const [code, setCode] = useState("");
  const [honeypot, setHoneypot] = useState("");
  const formRef = useRef<HTMLFormElement | null>(null);

  // Keep typed values when the schema changes (live preview); add the new fields.
  useEffect(() => {
    setValues((prev) => {
      const base = initialValues(fields);
      for (const key of Object.keys(base)) if (key in prev && typeof prev[key] === typeof base[key]) base[key] = prev[key]!;
      return base;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldKeys]);

  useEffect(() => {
    if (serverErrors && Object.keys(serverErrors).length) setErrors((prev) => ({ ...prev, ...serverErrors }));
  }, [serverErrors]);

  const today = useMemo(() => isoDay(0), []);
  const latest = useMemo(() => isoDay(DATE_MAX_DAYS), []);

  const set = (key: string, value: FormValues[string]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    if (errors[key]) setErrors(({ [key]: _gone, ...rest }) => rest);
  };

  const focusFirstError = (keys: string[]) => {
    requestAnimationFrame(() => {
      const form = formRef.current;
      if (!form) return;
      for (const key of keys) {
        const el = form.querySelector<HTMLElement>(`[data-field-key="${CSS.escape(key)}"]`);
        if (el) {
          el.focus();
          return;
        }
      }
    });
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (preview) return;
    const next = validateValues(fields, values);
    if (requiresAccessCode && !code.trim()) next.access_code = "Enter the access code you were given with the link.";
    if (!privacyAck) next.privacy_ack = "Tick this box to confirm you've read how your data is used.";
    setErrors(next);
    if (Object.keys(next).length) {
      focusFirstError([...fields.map((f) => f.key), "access_code", "privacy_ack"].filter((k) => next[k]));
      return;
    }
    onSubmit?.({
      answers: toAnswers(fields, values),
      privacy_ack: true,
      ...(requiresAccessCode ? { access_code: code.trim() } : {}),
      ...(honeypot ? { website: honeypot } : {}),
    });
  };

  const idOf = (key: string) => `${uid}-${key}`;

  const renderControl = (f: PublicFormField) => {
    const id = idOf(f.key);
    const error = errors[f.key];
    const describedBy = [f.help_text ? `${id}-help` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
    const common = {
      id,
      "data-field-key": f.key,
      "data-testid": `open-link-field-${f.key}`,
      "aria-invalid": error ? true : undefined,
      "aria-describedby": describedBy,
      "aria-required": f.required || undefined,
    } as const;
    const value = values[f.key];

    if (f.type === "checkbox") {
      return (
        <div className="flex items-start gap-2.5">
          <Checkbox {...common} checked={value === true} onCheckedChange={(checked) => set(f.key, checked === true)} invalid={Boolean(error)} className="mt-0.5" />
          <label htmlFor={id} className="text-sm leading-5 font-medium text-fg">
            {f.label}
            {f.required ? (
              <span className="ml-1 text-danger" aria-hidden="true">
                *
              </span>
            ) : null}
          </label>
        </div>
      );
    }
    if (f.type === "multiselect") {
      const list = Array.isArray(value) ? value : [];
      return (
        <div role="group" aria-labelledby={`${id}-label`} aria-describedby={describedBy} data-testid={common["data-testid"]} className="grid grid-cols-1 gap-2 pt-0.5">
          {f.options.map((option, i) => (
            <Checkbox
              key={option}
              id={`${id}-${i}`}
              data-field-key={i === 0 ? f.key : undefined}
              label={option}
              checked={list.includes(option)}
              invalid={Boolean(error)}
              onCheckedChange={(checked) => set(f.key, checked === true ? [...list, option] : list.filter((o) => o !== option))}
            />
          ))}
        </div>
      );
    }
    if (f.type === "select") {
      return (
        <div className="relative">
          <select
            {...common}
            value={typeof value === "string" ? value : ""}
            onChange={(e) => set(f.key, e.target.value)}
            className={cn(inputVariants({ size: "lg" }), "appearance-none pr-10", !value && "text-fg-subtle")}
          >
            <option value="">{f.placeholder || "Choose an option"}</option>
            {f.options.map((option) => (
              <option key={option} value={option} className="text-fg">
                {option}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute top-1/2 right-3.5 size-4 -translate-y-1/2 text-fg-subtle" aria-hidden="true" />
        </div>
      );
    }
    if (f.type === "textarea") {
      return (
        <Textarea
          {...common}
          value={typeof value === "string" ? value : ""}
          onChange={(e) => set(f.key, e.target.value)}
          placeholder={f.placeholder ?? undefined}
          maxLength={Math.min(f.max_length ?? 1000, 2000)}
          rows={4}
          autoResize
          maxRows={10}
        />
      );
    }
    return (
      <Input
        {...common}
        type={nativeType(f.type)}
        inputMode={inputMode(f.type)}
        size="lg"
        value={typeof value === "string" ? value : ""}
        onChange={(e) => set(f.key, e.target.value)}
        placeholder={f.placeholder ?? undefined}
        autoComplete={AUTOCOMPLETE[f.key] ?? "off"}
        autoCapitalize={f.type === "email" || f.type === "url" ? "none" : undefined}
        spellCheck={f.type === "email" || f.type === "url" ? false : undefined}
        min={f.type === "date" ? today : f.type === "number" && f.min !== null ? f.min : undefined}
        max={f.type === "date" ? latest : f.type === "number" && f.max !== null ? f.max : undefined}
        step={f.type === "number" ? "any" : undefined}
        maxLength={f.type === "text" ? Math.min(f.max_length ?? 200, 500) : f.type === "url" ? 500 : undefined}
      />
    );
  };

  return (
    <form ref={formRef} onSubmit={submit} noValidate className="grid grid-cols-1 gap-6" aria-label={preview ? "Registration form preview" : "Registration form"}>
      <div className="grid grid-cols-1 gap-5">
        {fields.map((f) => {
          const id = idOf(f.key);
          const error = errors[f.key];
          return (
            <div key={f.key} className="grid min-w-0 grid-cols-1 gap-1.5">
              {f.type !== "checkbox" ? (
                <Label htmlFor={f.type === "multiselect" ? undefined : id} id={`${id}-label`} required={f.required} optional={!f.required}>
                  {f.label}
                </Label>
              ) : null}
              {renderControl(f)}
              {f.help_text ? (
                <p id={`${id}-help`} className="text-xs leading-[1.125rem] text-fg-muted">
                  {f.help_text}
                </p>
              ) : null}
              {error ? (
                <p id={`${id}-error`} className="text-xs leading-[1.125rem] font-medium text-danger-fg" data-testid={`open-link-field-error-${f.key}`}>
                  {error}
                </p>
              ) : null}
            </div>
          );
        })}

        {requiresAccessCode ? (
          <div className="grid min-w-0 grid-cols-1 gap-1.5">
            <Label htmlFor={idOf("access_code")} required>
              Access code
            </Label>
            <Input
              id={idOf("access_code")}
              data-field-key="access_code"
              data-testid="open-link-access-code-input"
              size="lg"
              autoComplete="one-time-code"
              autoCapitalize="characters"
              spellCheck={false}
              value={code}
              onChange={(e) => {
                setCode(e.target.value);
                if (errors.access_code) setErrors(({ access_code: _gone, ...rest }) => rest);
              }}
              leading={<LockKeyhole />}
              className="font-mono tracking-[0.12em]"
              aria-invalid={errors.access_code ? true : undefined}
              aria-describedby={`${idOf("access_code")}-help`}
            />
            <p id={`${idOf("access_code")}-help`} className={errors.access_code ? "text-xs font-medium text-danger-fg" : "text-xs text-fg-muted"}>
              {errors.access_code ?? "The hiring team shares this code together with the link."}
            </p>
          </div>
        ) : null}

        {/* Honeypot: invisible to people and assistive tech; bots that fill every input reveal themselves. */}
        <div aria-hidden="true" className="sr-only">
          <label htmlFor={idOf("website")}>Website</label>
          <input id={idOf("website")} name="website" type="text" tabIndex={-1} autoComplete="off" value={honeypot} onChange={(e) => setHoneypot(e.target.value)} />
        </div>
      </div>

      {dataUse ? (
        <DataUsePanel dataUse={dataUse} organizationName={organizationName}>
          <div className="grid gap-1.5">
            <Checkbox
              data-testid="open-link-privacy-ack"
              data-field-key="privacy_ack"
              id={idOf("privacy_ack")}
              checked={privacyAck}
              onCheckedChange={(checked) => {
                setPrivacyAck(checked === true);
                if (errors.privacy_ack) setErrors(({ privacy_ack: _gone, ...rest }) => rest);
              }}
              invalid={Boolean(errors.privacy_ack)}
              label="I have read how my data will be used"
            />
            {errors.privacy_ack ? <p className="pl-6.5 text-xs font-medium text-danger-fg">{errors.privacy_ack}</p> : null}
          </div>
        </DataUsePanel>
      ) : null}

      <div aria-live="polite" className="empty:hidden">
        {formError}
      </div>

      <Button
        type="submit"
        size="lg"
        fullWidth
        loading={submitting}
        loadingText="Registering"
        data-testid="open-link-submit"
        disabled={preview}
        aria-disabled={preview || undefined}
        title={preview ? "Preview only" : undefined}
      >
        {submitLabel}
      </Button>
    </form>
  );
}
