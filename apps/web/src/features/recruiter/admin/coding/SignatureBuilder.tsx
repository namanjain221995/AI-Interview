import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { CODE_LANGUAGE_LABELS } from "@/lib/labels";
import type { CodeLanguage, CodingSignature, CodingValueType } from "@/lib/types";

import { FieldShell, NumberInput } from "../components/Field";
import { COMPARISONS, signaturePreview, VALUE_TYPES, type EditorSignature } from "./codingModel";

const TYPE_OPTIONS = VALUE_TYPES.map((t) => ({ value: t, label: t }));

export interface SignatureBuilderProps {
  value: EditorSignature;
  onChange: (value: EditorSignature) => void;
  languages: CodeLanguage[];
  errors: Record<string, string>;
  disabled?: boolean;
}

/** Language-neutral function signature with a live per-language preview. */
export function SignatureBuilder({ value, onChange, languages, errors, disabled }: SignatureBuilderProps) {
  const setParam = (index: number, patch: Partial<{ name: string; type: CodingValueType }>) =>
    onChange({ ...value, params: value.params.map((p, i) => (i === index ? { ...p, ...patch } : p)) });
  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= value.params.length) return;
    const params = [...value.params];
    [params[index], params[target]] = [params[target]!, params[index]!];
    onChange({ ...value, params });
  };
  const previewLanguages = languages.filter((l): l is Exclude<CodeLanguage, "sql"> => l !== "sql");

  return (
    <div className="grid gap-5">
      <div className="grid gap-4 sm:grid-cols-[1fr_11rem_13rem]">
        <FieldShell label="Function name" required error={errors["signature.function_name"]} description="snake_case; JavaScript, Java, Go and C++ use camelCase automatically.">
          {(control) => <Input {...control} value={value.function_name} disabled={disabled} className="font-mono" onChange={(e) => onChange({ ...value, function_name: e.target.value.trim() })} />}
        </FieldShell>
        <FieldShell label="Returns">
          {(control) => <Select<CodingValueType> id={control.id} value={value.return_type} options={TYPE_OPTIONS} disabled={disabled} onValueChange={(return_type) => onChange({ ...value, return_type })} />}
        </FieldShell>
        <FieldShell label="Compare results" error={errors["signature.comparison"]}>
          {(control) => (
            <Select<CodingSignature["comparison"]>
              id={control.id}
              value={value.comparison}
              options={COMPARISONS}
              disabled={disabled}
              onValueChange={(comparison) => onChange({ ...value, comparison, tolerance: comparison === "float_tolerance" ? value.tolerance : null })}
            />
          )}
        </FieldShell>
      </div>
      {value.comparison === "float_tolerance" ? (
        <FieldShell label="Tolerance" optional error={errors["signature.tolerance"]} description="Relative tolerance; leave empty for the default of 0.000001." className="max-w-56">
          {(control) => <NumberInput {...control} value={value.tolerance ?? null} nullable step={0.000001} placeholder="0.000001" disabled={disabled} onChange={(tolerance) => onChange({ ...value, tolerance })} />}
        </FieldShell>
      ) : null}

      <div className="grid gap-2">
        <span className="text-[13px] font-medium text-fg">Parameters</span>
        {errors["signature.params"] ? <p className="text-xs font-medium text-danger-fg">{errors["signature.params"]}</p> : null}
        <ol className="grid gap-2">
          {value.params.map((param, index) => (
            <li key={index} className="grid gap-1">
              <div className="grid grid-cols-[1.5rem_1fr_10rem_auto] items-center gap-2">
                <span className="text-right text-xs text-fg-subtle tabular-nums">{index + 1}</span>
                <Input
                  aria-label={`Parameter ${index + 1} name`}
                  value={param.name}
                  disabled={disabled}
                  className="font-mono"
                  aria-invalid={errors[`signature.params.${index}.name`] ? true : undefined}
                  onChange={(e) => setParam(index, { name: e.target.value.trim() })}
                />
                <Select<CodingValueType> aria-label={`Parameter ${index + 1} type`} value={param.type} options={TYPE_OPTIONS} disabled={disabled} onValueChange={(type) => setParam(index, { type })} />
                <div className="flex items-center">
                  <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Move parameter ${index + 1} up`} disabled={disabled || index === 0} onClick={() => move(index, -1)}>
                    <ArrowUp />
                  </Button>
                  <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Move parameter ${index + 1} down`} disabled={disabled || index === value.params.length - 1} onClick={() => move(index, 1)}>
                    <ArrowDown />
                  </Button>
                  <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Remove parameter ${index + 1}`} disabled={disabled} onClick={() => onChange({ ...value, params: value.params.filter((_, i) => i !== index) })}>
                    <Trash2 />
                  </Button>
                </div>
              </div>
              {errors[`signature.params.${index}.name`] ? <p className="pl-8 text-xs font-medium text-danger-fg">{errors[`signature.params.${index}.name`]}</p> : null}
            </li>
          ))}
        </ol>
        <Button
          variant="ghost"
          size="sm"
          className="w-fit text-fg-muted"
          leftIcon={<Plus />}
          disabled={disabled || value.params.length >= 6}
          onClick={() => onChange({ ...value, params: [...value.params, { name: `arg${value.params.length + 1}`, type: "int" }] })}
        >
          Add parameter
        </Button>
      </div>

      {previewLanguages.length ? (
        <div className="grid gap-1.5">
          <span className="text-[13px] font-medium text-fg">What candidates implement</span>
          <dl className="divide-y divide-line-subtle overflow-hidden rounded-md border border-line bg-surface-subtle">
            {previewLanguages.map((language) => (
              <div key={language} className="grid grid-cols-[6.5rem_1fr] items-baseline gap-3 px-3 py-1.5">
                <dt className="text-xs text-fg-muted">{CODE_LANGUAGE_LABELS[language]}</dt>
                <dd className="overflow-x-auto font-mono text-[12.5px] whitespace-pre text-fg [font-variant-ligatures:none] scrollbar-thin">{signaturePreview(value, language)}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </div>
  );
}
