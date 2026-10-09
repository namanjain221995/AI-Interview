import { Caption, Section, Specimen } from "../Specimen";

const SURFACES = [
  { name: "surface-muted", note: "App canvas", className: "bg-surface-muted" },
  { name: "surface", note: "Sheets, cards", className: "bg-surface" },
  { name: "surface-subtle", note: "Headers, wells", className: "bg-surface-subtle" },
  { name: "surface-raised", note: "Menus, dialogs", className: "bg-surface-raised" },
  { name: "surface-sunken", note: "Segmented wells, stage canvas", className: "bg-surface-sunken" },
  { name: "track", note: "Progress and meter tracks", className: "bg-track" },
  { name: "field", note: "Text inputs", className: "bg-field" },
];

const INK = [
  { name: "fg", note: "Body text, primary actions", className: "bg-fg" },
  { name: "fg-muted", note: "Secondary text", className: "bg-fg-muted" },
  { name: "fg-subtle", note: "Placeholders, icons", className: "bg-fg-subtle" },
  { name: "line-strong", note: "Control borders", className: "bg-line-strong" },
  { name: "line", note: "Hairlines", className: "bg-line" },
];

const MEANING = [
  { name: "accent", note: "Focus, links, selection", className: "bg-accent" },
  { name: "signal", note: "Live only", className: "bg-signal" },
  { name: "success", note: "Positive evidence", className: "bg-success" },
  { name: "warning", note: "Needs attention", className: "bg-warning" },
  { name: "danger", note: "Errors, gaps", className: "bg-danger" },
];

const CHART = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5", "bg-chart-6", "bg-chart-7", "bg-chart-8"];

function Swatches({ items }: { items: Array<{ name: string; note: string; className: string }> }) {
  return (
    <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {items.map((item) => (
        <li key={item.name} className="grid gap-2">
          <span className={`h-14 rounded-md border border-line ${item.className}`} />
          <span className="grid">
            <span className="font-mono text-xs text-fg">{item.name}</span>
            <span className="text-xs text-fg-muted">{item.note}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function FoundationsSection() {
  return (
    <Section
      id="foundations"
      title="Foundations"
      description="Ink-navy neutrals carry text and the primary action. Brand blue marks interaction, cyan is reserved for live states, and semantic colours appear only when they mean something."
    >
      <Specimen title="Surfaces and ink" note="Semantic tokens re-theme in the dark theme and on the stage surface">
        <div className="grid gap-6">
          <Swatches items={SURFACES} />
          <Swatches items={INK} />
        </div>
      </Specimen>
      <Specimen title="Meaning" note="Never the only signal: always paired with an icon or label">
        <Swatches items={MEANING} />
      </Specimen>
      <Specimen title="Chart slots" note="Validated for colour-vision deficiency, assigned by entity in this order">
        <div className="flex gap-1.5">
          {CHART.map((className, index) => (
            <div key={className} className="grid flex-1 gap-1.5">
              <span className={`h-10 rounded-sm ${className}`} />
              <span className="text-center text-xs text-fg-muted tabular-nums">{index + 1}</span>
            </div>
          ))}
        </div>
      </Specimen>
      <Specimen title="Type" note="Inter with optical sizing, 14 px UI base; JetBrains Mono for code only">
        <div className="grid gap-5">
          <div className="grid gap-1">
            <p className="text-display text-fg">Evidence before opinion</p>
            <Caption>Display, 36/42, 640</Caption>
          </div>
          <div className="grid gap-1">
            <p className="text-title text-fg">Senior Platform Engineer</p>
            <Caption>Title, 24/32, 600</Caption>
          </div>
          <div className="grid gap-1">
            <p className="text-heading text-fg">Competency findings</p>
            <Caption>Heading, 17/24, 600</Caption>
          </div>
          <div className="grid max-w-2xl gap-1">
            <p className="text-sm text-fg">
              The candidate described a write-through cache with explicit invalidation on schema changes, and quantified the p99 latency before and after the
              rollout. They did not describe a rollback plan.
            </p>
            <Caption>Body, 14/20, 400</Caption>
          </div>
          <div className="grid gap-1">
            <p className="font-mono text-[13px] text-fg">kubectl rollout undo deployment/api --to-revision=41</p>
            <Caption>Mono, 13/24, code and identifiers</Caption>
          </div>
        </div>
      </Specimen>
      <Specimen title="Radius and elevation" note="Radius grows with hierarchy">
        <div className="flex flex-wrap items-end gap-6">
          {[
            ["rounded-xs", "Tag 4"],
            ["rounded-sm", "Badge 6"],
            ["rounded-control", "Control 8"],
            ["rounded-md", "Menu 10"],
            ["rounded-lg", "Card 14"],
            ["rounded-xl", "Dialog 20"],
          ].map(([radius, label]) => (
            <div key={radius} className="grid justify-items-center gap-2">
              <span className={`size-16 border border-line-strong bg-surface-subtle ${radius}`} />
              <span className="text-xs text-fg-muted">{label}</span>
            </div>
          ))}
          <div className="ml-4 flex gap-6">
            {[
              ["shadow-card", "Card"],
              ["shadow-raised", "Raised"],
              ["shadow-overlay", "Overlay"],
            ].map(([shadow, label]) => (
              <div key={shadow} className="grid justify-items-center gap-2">
                <span className={`size-16 rounded-lg bg-surface ${shadow}`} />
                <span className="text-xs text-fg-muted">{label}</span>
              </div>
            ))}
          </div>
        </div>
      </Specimen>
    </Section>
  );
}
