/**
 * Searchable single select (Combobox) and tag input (MultiSelect).
 *
 * Both follow the ARIA 1.2 combobox pattern: the text input owns focus and
 * points at the active option with aria-activedescendant; the listbox opens
 * in a popover anchored to the field.
 */

import * as PopoverPrimitive from "@radix-ui/react-popover";
import { Check, ChevronDown, Plus, X } from "lucide-react";
import { useCallback, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { fieldBase } from "./Input";

export interface ComboboxOption {
  value: string;
  label: string;
  description?: string;
  /** Extra text matched by the filter (synonyms, ids). */
  keywords?: string;
  disabled?: boolean;
}

function matches(option: ComboboxOption, query: string): boolean {
  if (!query) return true;
  const haystack = `${option.label} ${option.value} ${option.keywords ?? ""}`.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => haystack.includes(term));
}

interface ListboxProps {
  id: string;
  options: ComboboxOption[];
  activeIndex: number;
  isSelected: (value: string) => boolean;
  onPick: (option: ComboboxOption) => void;
  onHover: (index: number) => void;
  createLabel?: string | null;
  createActive?: boolean;
  onCreate?: () => void;
  emptyMessage: ReactNode;
  multiple?: boolean;
}

function optionId(listId: string, index: number) {
  return `${listId}-opt-${index}`;
}

function Listbox({ id, options, activeIndex, isSelected, onPick, onHover, createLabel, createActive, onCreate, emptyMessage, multiple }: ListboxProps) {
  return (
    <ul id={id} role="listbox" aria-multiselectable={multiple || undefined} className="max-h-64 overflow-y-auto p-1 scrollbar-thin">
      {options.map((option, index) => {
        const selected = isSelected(option.value);
        return (
          <li
            key={option.value}
            id={optionId(id, index)}
            role="option"
            aria-selected={selected}
            aria-disabled={option.disabled || undefined}
            data-active={index === activeIndex || undefined}
            onMouseMove={() => onHover(index)}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => !option.disabled && onPick(option)}
            className={cn(
              "relative flex cursor-default select-none items-start gap-2 rounded-sm py-1.5 pr-2 pl-8 text-sm",
              "data-[active]:bg-surface-hover aria-disabled:opacity-50",
            )}
          >
            <span className="absolute left-2 top-2 flex size-4 items-center justify-center">
              {selected ? <Check className="size-4" strokeWidth={2.25} aria-hidden="true" /> : null}
            </span>
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{option.label}</span>
              {option.description ? <span className="text-xs text-fg-muted">{option.description}</span> : null}
            </span>
          </li>
        );
      })}
      {createLabel ? (
        <li
          id={optionId(id, options.length)}
          role="option"
          aria-selected={false}
          data-active={createActive || undefined}
          onMouseMove={() => onHover(options.length)}
          onMouseDown={(event) => event.preventDefault()}
          onClick={onCreate}
          className="relative flex cursor-default select-none items-center gap-2 rounded-sm py-1.5 pr-2 pl-8 text-sm data-[active]:bg-surface-hover"
        >
          <Plus className="absolute left-2 size-4 text-fg-muted" aria-hidden="true" />
          <span className="truncate">
            Add <span className="font-medium">“{createLabel}”</span>
          </span>
        </li>
      ) : null}
      {options.length === 0 && !createLabel ? <li className="px-2 py-6 text-center text-sm text-fg-muted">{emptyMessage}</li> : null}
    </ul>
  );
}

const popoverContentClass = cn(
  "z-50 w-(--radix-popover-trigger-width) min-w-48 overflow-hidden rounded-md border border-line bg-surface-raised text-fg shadow-overlay outline-none",
  "animate-float-in data-[side=top]:[--float-y:-4px]",
);

// ------------------------------------------------------------------ Combobox

export interface ComboboxProps {
  options: ComboboxOption[];
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder?: string;
  emptyMessage?: ReactNode;
  disabled?: boolean;
  invalid?: boolean;
  clearable?: boolean;
  id?: string;
  className?: string;
  onBlur?: () => void;
  "aria-label"?: string;
  "aria-describedby"?: string;
}

export function Combobox({
  options,
  value,
  onChange,
  placeholder = "Search…",
  emptyMessage = "No matches",
  disabled,
  invalid,
  clearable = true,
  id,
  className,
  onBlur,
  ...aria
}: ComboboxProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const listId = `${inputId}-listbox`;
  const selected = options.find((o) => o.value === value) ?? null;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => options.filter((o) => matches(o, query)), [options, query]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  const pick = (option: ComboboxOption) => {
    onChange(option.value);
    close();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!open) setOpen(true);
      setActiveIndex((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      if (open && filtered[activeIndex] && !filtered[activeIndex].disabled) {
        event.preventDefault();
        pick(filtered[activeIndex]);
      }
    } else if (event.key === "Escape") {
      if (open) {
        event.preventDefault();
        close();
      }
    }
  };

  return (
    <PopoverPrimitive.Root open={open && !disabled} onOpenChange={(next) => (next ? setOpen(true) : close())}>
      <PopoverPrimitive.Anchor asChild>
        <div className={cn("relative w-full", className)}>
          <input
            ref={inputRef}
            id={inputId}
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={open && filtered.length ? optionId(listId, activeIndex) : undefined}
            aria-invalid={invalid || undefined}
            autoComplete="off"
            disabled={disabled}
            placeholder={selected ? selected.label : placeholder}
            value={open ? query : (selected?.label ?? "")}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
              setOpen(true);
            }}
            onFocus={() => setActiveIndex(Math.max(0, filtered.findIndex((o) => o.value === value)))}
            onClick={() => setOpen(true)}
            onBlur={() => {
              close();
              onBlur?.();
            }}
            onKeyDown={onKeyDown}
            className={cn(fieldBase, "h-9 px-3 pr-14 text-sm", selected && !open ? "placeholder:text-fg" : undefined)}
            {...aria}
          />
          <span className="absolute inset-y-0 right-1.5 flex items-center gap-0.5">
            {clearable && selected && !disabled ? (
              <button
                type="button"
                aria-label="Clear selection"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  onChange(null);
                  inputRef.current?.focus();
                }}
                className="flex size-6 items-center justify-center rounded-sm text-fg-subtle hover:bg-surface-hover hover:text-fg"
              >
                <X className="size-3.5" />
              </button>
            ) : null}
            <ChevronDown className="pointer-events-none mr-1 size-4 text-fg-subtle" aria-hidden="true" />
          </span>
        </div>
      </PopoverPrimitive.Anchor>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={4}
          collisionPadding={8}
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => {
            if (event.target instanceof Node && inputRef.current?.parentElement?.contains(event.target)) event.preventDefault();
          }}
          className={popoverContentClass}
        >
          <Listbox
            id={listId}
            options={filtered}
            activeIndex={activeIndex}
            isSelected={(v) => v === value}
            onPick={pick}
            onHover={setActiveIndex}
            emptyMessage={emptyMessage}
          />
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

// --------------------------------------------------------------- MultiSelect

export interface MultiSelectProps {
  value: string[];
  onChange: (value: string[]) => void;
  /** Suggestions; values not in the list render with their raw value as label. */
  options?: ComboboxOption[];
  /** Allow free-text entries (e.g. skills). */
  creatable?: boolean;
  /** Normalise free-text before adding (default: trim + collapse spaces). */
  normalize?: (raw: string) => string;
  maxItems?: number;
  placeholder?: string;
  emptyMessage?: ReactNode;
  disabled?: boolean;
  invalid?: boolean;
  id?: string;
  className?: string;
  onBlur?: () => void;
  "aria-label"?: string;
  "aria-describedby"?: string;
}

const defaultNormalize = (raw: string) => raw.replace(/\s+/g, " ").trim();

export function MultiSelect({
  value,
  onChange,
  options = [],
  creatable = false,
  normalize = defaultNormalize,
  maxItems,
  placeholder = "Add…",
  emptyMessage = "No matches",
  disabled,
  invalid,
  id,
  className,
  onBlur,
  ...aria
}: MultiSelectProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const listId = `${inputId}-listbox`;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const fieldRef = useRef<HTMLDivElement>(null);

  const labelFor = useCallback((v: string) => options.find((o) => o.value === v)?.label ?? v, [options]);
  const atLimit = maxItems !== undefined && value.length >= maxItems;
  const filtered = useMemo(() => options.filter((o) => matches(o, query)), [options, query]);
  const normalizedQuery = normalize(query);
  const lowerValues = useMemo(() => new Set(value.map((v) => v.toLowerCase())), [value]);
  const exactOption = options.some((o) => o.label.toLowerCase() === normalizedQuery.toLowerCase() || o.value.toLowerCase() === normalizedQuery.toLowerCase());
  const createLabel = creatable && normalizedQuery && !exactOption && !lowerValues.has(normalizedQuery.toLowerCase()) && !atLimit ? normalizedQuery : null;
  const itemCount = filtered.length + (createLabel ? 1 : 0);

  const add = (raw: string) => {
    const next = raw.trim();
    if (!next || atLimit) return;
    if (lowerValues.has(next.toLowerCase())) return;
    onChange([...value, next]);
    setQuery("");
    setActiveIndex(0);
  };

  const remove = (target: string) => onChange(value.filter((v) => v !== target));

  const toggle = (option: ComboboxOption) => {
    if (value.includes(option.value)) remove(option.value);
    else add(option.value);
    inputRef.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((i) => Math.min(i + 1, Math.max(itemCount - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (open && activeIndex < filtered.length && filtered[activeIndex]) {
        const option = filtered[activeIndex];
        if (!option.disabled) toggle(option);
      } else if (createLabel) {
        add(createLabel);
      }
    } else if ((event.key === "," || event.key === "Tab") && creatable && createLabel) {
      event.preventDefault();
      add(createLabel);
    } else if (event.key === "Backspace" && query === "" && value.length > 0) {
      event.preventDefault();
      remove(value[value.length - 1]!);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
    }
  };

  const showList = open && !disabled && (itemCount > 0 || options.length > 0 || Boolean(query));

  return (
    <PopoverPrimitive.Root open={showList} onOpenChange={setOpen}>
      <PopoverPrimitive.Anchor asChild>
        <div
          ref={fieldRef}
          onMouseDown={(event) => {
            if (event.target === fieldRef.current) {
              event.preventDefault();
              inputRef.current?.focus();
            }
          }}
          aria-disabled={disabled || undefined}
          data-invalid={invalid || undefined}
          className={cn(
            fieldBase,
            "flex min-h-9 flex-wrap items-center gap-1.5 px-1.5 py-1 text-sm",
            "focus-within:border-accent focus-within:ring-3 focus-within:ring-accent/15",
            "data-[invalid]:border-danger aria-disabled:cursor-not-allowed aria-disabled:bg-surface-subtle",
            className,
          )}
        >
          {value.map((item) => (
            <span
              key={item}
              className="inline-flex h-6 max-w-full items-center gap-1 rounded-sm border border-neutral-line bg-neutral-soft pr-0.5 pl-2 text-xs font-medium text-neutral-fg"
            >
              <span className="truncate">{labelFor(item)}</span>
              {!disabled ? (
                <button
                  type="button"
                  aria-label={`Remove ${labelFor(item)}`}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => remove(item)}
                  className="flex size-5 items-center justify-center rounded-xs text-fg-subtle hover:bg-surface-active hover:text-fg"
                >
                  <X className="size-3" />
                </button>
              ) : null}
            </span>
          ))}
          <input
            ref={inputRef}
            id={inputId}
            role="combobox"
            aria-expanded={showList}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={showList && itemCount ? optionId(listId, activeIndex) : undefined}
            aria-invalid={invalid || undefined}
            autoComplete="off"
            disabled={disabled}
            value={query}
            placeholder={value.length === 0 ? placeholder : atLimit ? "" : undefined}
            readOnly={atLimit}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => {
              setOpen(false);
              onBlur?.();
            }}
            onKeyDown={onKeyDown}
            className="h-6 min-w-24 flex-1 bg-transparent px-1.5 text-sm text-fg outline-none placeholder:text-fg-subtle disabled:cursor-not-allowed"
            {...aria}
          />
        </div>
      </PopoverPrimitive.Anchor>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={4}
          collisionPadding={8}
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => {
            if (event.target instanceof Node && fieldRef.current?.contains(event.target)) event.preventDefault();
          }}
          className={popoverContentClass}
        >
          <Listbox
            id={listId}
            options={filtered}
            activeIndex={activeIndex}
            isSelected={(v) => value.includes(v)}
            onPick={toggle}
            onHover={setActiveIndex}
            createLabel={createLabel}
            createActive={activeIndex === filtered.length}
            onCreate={() => createLabel && add(createLabel)}
            emptyMessage={creatable ? "Type to add a new entry" : emptyMessage}
            multiple
          />
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
