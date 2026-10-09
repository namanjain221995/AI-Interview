/** Small controls shared by the avatar playground panels (dev tool). */
import type { ReactNode } from "react";

export function Chip({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "green" | "amber" | "red" }) {
  const tones = {
    slate: "bg-slate-700/60 text-slate-200",
    green: "bg-emerald-600/30 text-emerald-200",
    amber: "bg-amber-600/30 text-amber-100",
    red: "bg-red-600/30 text-red-100",
  };
  return <span className={`rounded-md px-2 py-0.5 font-mono text-xs ${tones[tone]}`}>{children}</span>;
}

export function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-medium text-slate-400">{title}</h3>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </section>
  );
}

export function Btn({
  active,
  disabled,
  onClick,
  children,
  testId,
}: {
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      disabled={disabled}
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-md border px-2.5 py-1 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-cyan-300 disabled:opacity-40 ${
        active ? "border-cyan-400/70 bg-cyan-500/20 text-cyan-100" : "border-slate-600/60 bg-slate-800/70 text-slate-200 hover:bg-slate-700/70"
      }`}
    >
      {children}
    </button>
  );
}
