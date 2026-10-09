/**
 * react-hook-form + zod helpers.
 *
 *   const form = useZodForm(schema, { defaultValues });
 *   <Form form={form} onSubmit={save}>
 *     <FormField name="email" label="Work email" required>
 *       {(field) => <Input type="email" {...field} />}
 *     </FormField>
 *     <FormError />
 *   </Form>
 *
 * FormField wires id, aria-invalid, aria-describedby, the label and the error
 * message, so every form in the product announces errors the same way.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle } from "lucide-react";
import { useId, type FormHTMLAttributes, type ReactNode, type Ref } from "react";
import {
  FormProvider,
  useController,
  useForm,
  useFormContext,
  type FieldPath,
  type FieldValues,
  type Resolver,
  type SubmitHandler,
  type UseFormProps,
  type UseFormReturn,
} from "react-hook-form";
import type { z } from "zod";

import { isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { Callout } from "./Callout";
import { Label } from "./Label";

export function useZodForm<TSchema extends z.ZodType<FieldValues, FieldValues>>(
  schema: TSchema,
  options?: Omit<UseFormProps<z.input<TSchema>, unknown, z.output<TSchema>>, "resolver">,
): UseFormReturn<z.input<TSchema>, unknown, z.output<TSchema>> {
  return useForm<z.input<TSchema>, unknown, z.output<TSchema>>({
    mode: "onTouched",
    ...options,
    // zod 4 schemas also satisfy the zod 3 overload, which erases the generic types.
    resolver: zodResolver(schema) as unknown as Resolver<z.input<TSchema>, unknown, z.output<TSchema>>,
  });
}

export interface FormProps<TInput extends FieldValues, TOutput extends FieldValues>
  extends Omit<FormHTMLAttributes<HTMLFormElement>, "onSubmit"> {
  form: UseFormReturn<TInput, unknown, TOutput>;
  onSubmit: SubmitHandler<TOutput>;
}

export function Form<TInput extends FieldValues, TOutput extends FieldValues>({
  form,
  onSubmit,
  children,
  className,
  ...props
}: FormProps<TInput, TOutput>) {
  return (
    <FormProvider {...form}>
      <form noValidate onSubmit={form.handleSubmit(onSubmit)} className={cn("space-y-5", className)} {...props}>
        {children}
      </form>
    </FormProvider>
  );
}

export interface FieldControlProps {
  id: string;
  name: string;
  value: unknown;
  onChange: (...event: unknown[]) => void;
  onBlur: () => void;
  ref: Ref<never>;
  disabled?: boolean;
  "aria-invalid": boolean | undefined;
  "aria-describedby": string | undefined;
  "aria-required": boolean | undefined;
}

export interface FormFieldProps<TValues extends FieldValues, TName extends FieldPath<TValues>> {
  name: TName;
  label?: ReactNode;
  description?: ReactNode;
  required?: boolean;
  optional?: boolean;
  /** Keep the label for assistive technology only. */
  hideLabel?: boolean;
  disabled?: boolean;
  className?: string;
  /** Render the control; spread the props onto it. */
  children: (field: FieldControlProps, state: { error?: string; invalid: boolean }) => ReactNode;
}

export function FormField<TValues extends FieldValues = FieldValues, TName extends FieldPath<TValues> = FieldPath<TValues>>({
  name,
  label,
  description,
  required,
  optional,
  hideLabel,
  disabled,
  className,
  children,
}: FormFieldProps<TValues, TName>) {
  const { control } = useFormContext<TValues>();
  const { field, fieldState } = useController<TValues, TName>({ name, control, disabled });
  const id = useId();
  const descriptionId = description ? `${id}-description` : undefined;
  const errorId = fieldState.error ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;
  const errorMessage = fieldState.error?.message;

  const controlProps: FieldControlProps = {
    id,
    name: field.name,
    value: field.value ?? "",
    onChange: field.onChange as (...event: unknown[]) => void,
    onBlur: field.onBlur,
    ref: field.ref as Ref<never>,
    disabled: field.disabled,
    "aria-invalid": fieldState.invalid || undefined,
    "aria-describedby": describedBy,
    "aria-required": required || undefined,
  };

  return (
    <div className={cn("grid content-start gap-1.5", className)} data-invalid={fieldState.invalid || undefined}>
      {label ? (
        <Label htmlFor={id} required={required} optional={optional} className={hideLabel ? "sr-only" : undefined}>
          {label}
        </Label>
      ) : null}
      {children(controlProps, { error: errorMessage, invalid: fieldState.invalid })}
      {description && !errorMessage ? (
        <p id={descriptionId} className="text-xs leading-[1.125rem] text-fg-muted">
          {description}
        </p>
      ) : null}
      {description && errorMessage ? (
        <p id={descriptionId} className="sr-only">
          {description}
        </p>
      ) : null}
      <FieldError id={errorId} message={errorMessage} />
    </div>
  );
}

export function FieldError({ id, message }: { id?: string; message?: string }) {
  return (
    <div aria-live="polite" className="empty:hidden">
      {message ? (
        <p id={id} className="flex items-start gap-1.5 text-xs leading-[1.125rem] font-medium text-danger-fg">
          <AlertCircle className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          {message}
        </p>
      ) : null}
    </div>
  );
}

/** Renders `errors.root.server` (set by applyServerErrors) as a callout. */
export function FormError({ title, className }: { title?: string; className?: string }) {
  const {
    formState: { errors },
  } = useFormContext();
  const rootError = (errors.root as Record<string, { message?: string; type?: string }> | undefined)?.server;
  if (!rootError?.message) return null;
  return (
    <Callout tone="danger" title={title} className={className}>
      {rootError.message}
    </Callout>
  );
}

/**
 * Maps an API validation error onto form fields (`details.fields` or
 * pydantic-style `details.errors`). Anything that doesn't map to a known
 * field is shown as the form-level server error. Returns true if handled.
 */
export function applyServerErrors<T extends FieldValues>(form: UseFormReturn<T, unknown, FieldValues> | UseFormReturn<T>, error: unknown): boolean {
  const target = form as UseFormReturn<T>;
  if (!isApiError(error)) {
    target.setError("root.server" as FieldPath<T>, { type: "server", message: "Something went wrong. Try again." });
    return false;
  }
  const fieldErrors = error.fieldErrors();
  const known = new Set(Object.keys(target.getValues() ?? {}));
  let mapped = 0;
  for (const [field, message] of Object.entries(fieldErrors)) {
    const top = field.split(".")[0] ?? field;
    if (known.has(top)) {
      target.setError(field as FieldPath<T>, { type: "server", message });
      mapped += 1;
    }
  }
  if (mapped === 0) {
    const suffix = error.requestId ? ` (request ID ${error.requestId})` : "";
    target.setError("root.server" as FieldPath<T>, { type: "server", message: `${error.message}${error.isServerError ? suffix : ""}` });
  }
  return true;
}
