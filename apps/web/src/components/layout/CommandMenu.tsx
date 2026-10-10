import * as DialogPrimitive from "@radix-ui/react-dialog";
import { CornerDownLeft, Plus, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { Kbd, modKeyLabel } from "@/components/ui/Kbd";
import { overlayClass } from "@/components/ui/Dialog";
import { hasFeature, useAuthStore, usePermissions } from "@/lib/auth";
import { useEventListener } from "@/lib/hooks";
import type { Permission } from "@/lib/types";
import { cn } from "@/lib/utils";

import { visibleNav } from "./staffNav";

interface CommandItem {
  key: string;
  label: string;
  group: string;
  to: string;
  icon: ReactNode;
  keywords?: string;
}

const ACTIONS: Array<{ key: string; label: string; to: string; permission: Permission; keywords: string }> = [
  { key: "new-job", label: "Create a job", to: "/app/jobs/new", permission: "jobs:write", keywords: "add role position" },
  { key: "new-candidate", label: "Add a candidate", to: "/app/candidates?new=1", permission: "candidates:write", keywords: "upload resume person" },
];

function score(item: CommandItem, query: string): number {
  if (!query) return 1;
  const q = query.toLowerCase();
  const label = item.label.toLowerCase();
  if (label.startsWith(q)) return 3;
  if (label.includes(q)) return 2;
  if ((item.keywords ?? "").toLowerCase().includes(q)) return 1;
  return 0;
}

export interface CommandMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** ⌘K palette for jumping between staff pages and starting common actions. */
export function CommandMenu({ open, onOpenChange }: CommandMenuProps) {
  const navigate = useNavigate();
  const permissions = usePermissions();
  const user = useAuthStore((st) => st.user);
  const organization = useAuthStore((st) => st.organization);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const listRef = useRef<HTMLUListElement>(null);

  const items = useMemo<CommandItem[]>(() => {
    const pages = visibleNav(permissions, (feature) => hasFeature({ user, organization }, feature)).flatMap((group) =>
      group.items.map((item) => {
        const Icon = item.icon;
        return { key: item.key, label: item.label, group: "Go to", to: item.to, icon: <Icon />, keywords: item.keywords };
      }),
    );
    const actions = ACTIONS.filter((a) => permissions.includes(a.permission)).map((a) => ({
      key: a.key,
      label: a.label,
      group: "Actions",
      to: a.to,
      icon: <Plus />,
      keywords: a.keywords,
    }));
    return [...actions, ...pages];
  }, [permissions, user, organization]);

  const results = useMemo(
    () =>
      items
        .map((item) => ({ item, s: score(item, query.trim()) }))
        .filter((r) => r.s > 0)
        .sort((a, b) => b.s - a.s)
        .map((r) => r.item),
    [items, query],
  );

  useEffect(() => {
    if (!open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const go = (item: CommandItem | undefined) => {
    if (!item) return;
    onOpenChange(false);
    navigate(item.to);
  };

  const grouped = results.reduce<Record<string, CommandItem[]>>((acc, item) => {
    (acc[item.group] ??= []).push(item);
    return acc;
  }, {});
  let runningIndex = -1;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className={cn(overlayClass, "place-items-start pt-[12vh] sm:pt-[12vh]")}>
          <DialogPrimitive.Content
            aria-describedby={undefined}
            className="relative mx-auto w-full max-w-[560px] overflow-hidden rounded-xl border border-line bg-surface-raised shadow-overlay outline-none data-[state=open]:animate-pop-in data-[state=closed]:animate-pop-out"
          >
            <DialogPrimitive.Title className="sr-only">Search and jump to</DialogPrimitive.Title>
            <div className="flex items-center gap-3 border-b border-line-subtle px-4">
              <Search className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
              <input
                autoFocus
                role="combobox"
                aria-expanded="true"
                aria-controls={listId}
                aria-activedescendant={results[active] ? `${listId}-${active}` : undefined}
                aria-autocomplete="list"
                placeholder="Search pages and actions…"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActive(0);
                }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setActive((i) => Math.min(i + 1, results.length - 1));
                  } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setActive((i) => Math.max(i - 1, 0));
                  } else if (event.key === "Enter") {
                    event.preventDefault();
                    go(results[active]);
                  }
                }}
                className="h-12 w-full bg-transparent text-[15px] text-fg outline-none placeholder:text-fg-subtle"
              />
              <Kbd>Esc</Kbd>
            </div>
            <ul ref={listRef} id={listId} role="listbox" className="max-h-[min(380px,60vh)] overflow-y-auto p-1.5 scrollbar-thin">
              {results.length === 0 ? (
                <li className="px-3 py-10 text-center text-sm text-fg-muted">Nothing matches “{query}”.</li>
              ) : (
                Object.entries(grouped).map(([group, groupItems]) => (
                  <li key={group} role="presentation">
                    <p className="px-2.5 pt-2 pb-1 text-xs font-medium text-fg-subtle">{group}</p>
                    <ul role="presentation">
                      {groupItems.map((item) => {
                        runningIndex += 1;
                        const index = runningIndex;
                        return (
                          <li
                            key={item.key}
                            id={`${listId}-${index}`}
                            role="option"
                            aria-selected={index === active}
                            data-index={index}
                            onMouseMove={() => setActive(index)}
                            onClick={() => go(item)}
                            className={cn(
                              "flex h-10 cursor-default items-center gap-3 rounded-control px-2.5 text-sm text-fg [&_svg]:size-4 [&_svg]:text-fg-muted",
                              index === active && "bg-surface-hover",
                            )}
                          >
                            {item.icon}
                            <span className="flex-1 truncate">{item.label}</span>
                            {index === active ? <CornerDownLeft className="size-3.5! text-fg-subtle!" aria-hidden="true" /> : null}
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                ))
              )}
            </ul>
          </DialogPrimitive.Content>
        </DialogPrimitive.Overlay>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Search-field-shaped trigger for the top bar; also binds ⌘K / Ctrl+K. */
export function CommandMenuTrigger({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const mod = useMemo(modKeyLabel, []);
  useEventListener("keydown", (event: KeyboardEvent) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      setOpen((value) => !value);
    }
  });
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "flex h-9 w-full max-w-sm items-center gap-2.5 rounded-control border border-line bg-surface-subtle pr-1.5 pl-3 text-sm text-fg-subtle transition-colors hover:border-line-strong hover:bg-surface",
          className,
        )}
        aria-label="Search and jump to"
        aria-keyshortcuts="Meta+K Control+K"
      >
        <Search className="size-4 shrink-0" aria-hidden="true" />
        <span className="flex-1 truncate text-left">Search or jump to…</span>
        <span className="hidden items-center gap-0.5 sm:flex" aria-hidden="true">
          <Kbd>{mod}</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>
      <CommandMenu open={open} onOpenChange={setOpen} />
    </>
  );
}
