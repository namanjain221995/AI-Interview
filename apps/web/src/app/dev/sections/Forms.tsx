import { Clock3, Code2, Mail, MessagesSquare, Search, Workflow } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Checkbox } from "@/components/ui/Checkbox";
import { Combobox, MultiSelect } from "@/components/ui/Combobox";
import { FileDropzone, type DropzoneStatus } from "@/components/ui/FileDropzone";
import { FieldError } from "@/components/ui/Form";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { RadioCard, RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { Select } from "@/components/ui/Select";
import { Slider } from "@/components/ui/Slider";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";

import { JOB_OPTIONS, SKILL_OPTIONS } from "../showcaseData";
import { Section, Specimen } from "../Specimen";

function UploadDemo() {
  const [status, setStatus] = useState<DropzoneStatus>("idle");
  const [progress, setProgress] = useState<number>(0);
  const [file, setFile] = useState<{ name: string; size: number } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearInterval(timer.current);
  }, []);

  const start = (picked: File) => {
    setFile({ name: picked.name, size: picked.size });
    setStatus("uploading");
    setProgress(0);
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(() => {
      setProgress((value) => {
        const next = Math.min(100, value + 9 + Math.random() * 14);
        if (next >= 100 && timer.current) {
          clearInterval(timer.current);
          setStatus("success");
        }
        return next;
      });
    }, 180);
  };

  return (
    <FileDropzone
      accept={["application/pdf", ".docx", ".doc", ".txt"]}
      acceptLabel="PDF, DOCX or TXT"
      maxSizeBytes={10 * 1024 * 1024}
      title="Drop your resume here or browse"
      hint="PDF, DOCX or TXT up to 10 MB"
      status={status}
      progress={progress}
      file={file}
      onFiles={(files) => files[0] && start(files[0])}
      onCancel={() => {
        if (timer.current) clearInterval(timer.current);
        setStatus("idle");
        setFile(null);
      }}
    />
  );
}

export function FormsSection() {
  const [job, setJob] = useState<string | null>("platform");
  const [skills, setSkills] = useState<string[]>(["kubernetes", "go", "Incident command"]);
  const [round, setRound] = useState("technical");
  const [threshold, setThreshold] = useState([70]);
  const [notify, setNotify] = useState(true);

  return (
    <Section
      id="forms"
      title="Form controls"
      description="Labels sit above fields, errors say how to fix the problem, and every control works with the keyboard alone."
    >
      <Specimen title="Text entry">
        <div className="grid gap-5 md:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor="ds-email" required>
              Work email
            </Label>
            <Input id="ds-email" type="email" placeholder="name@company.com" leading={<Mail />} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ds-search">Search candidates</Label>
            <Input id="ds-search" placeholder="Name, email or skill" leading={<Search />} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ds-title" required>
              Job title
            </Label>
            <Input id="ds-title" defaultValue="Senior Platform Engineer" aria-invalid aria-describedby="ds-title-error" />
            <FieldError id="ds-title-error" message="A job with this title is already open. Add a team or location to tell them apart." />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ds-disabled" optional>
              External reference
            </Label>
            <Input id="ds-disabled" disabled defaultValue="REQ-2041" />
          </div>
          <div className="grid gap-1.5 md:col-span-2">
            <Label htmlFor="ds-notes">Interviewer notes</Label>
            <Textarea
              id="ds-notes"
              autoResize
              showCount
              maxLength={400}
              defaultValue="Probe how they handled the database migration: expect specifics on dual writes, backfill and rollback."
            />
          </div>
        </div>
      </Specimen>

      <Specimen title="Choice">
        <div className="grid gap-6 md:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor="ds-select">Job</Label>
            <Select id="ds-select" options={JOB_OPTIONS} value={job} onValueChange={setJob} placeholder="Choose a job" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ds-combo">Job (searchable)</Label>
            <Combobox id="ds-combo" options={JOB_OPTIONS} value={job} onChange={setJob} placeholder="Search jobs…" />
          </div>
          <div className="grid gap-1.5 md:col-span-2">
            <Label htmlFor="ds-skills">Required skills</Label>
            <MultiSelect id="ds-skills" options={SKILL_OPTIONS} value={skills} onChange={setSkills} creatable placeholder="Add a skill…" />
            <p className="text-xs text-fg-muted">Pick from the list or type your own and press Enter. Backspace removes the last skill.</p>
          </div>
          <div className="grid content-start gap-3">
            <Checkbox defaultChecked label="Allow typed answers" description="Candidates can type instead of speaking." />
            <Checkbox label="Record answer audio" description="Kept for 90 days, then deleted." />
            <Checkbox checked="indeterminate" label="Monitoring signals" description="Some signals are enabled." />
          </div>
          <div className="grid content-start gap-4">
            <Switch checked={notify} onCheckedChange={setNotify} labelPosition="start" label="Email me when a report is ready" description="One message per completed interview." />
            <Switch defaultChecked size="sm" labelPosition="start" label="Show live captions" />
            <div className="grid gap-2">
              <Label>Passing threshold</Label>
              <Slider value={threshold} onValueChange={setThreshold} min={40} max={95} step={1} thumbLabels={["Passing threshold"]} showValue formatValue={(v) => `${v} / 100`} />
            </div>
          </div>
        </div>
      </Specimen>

      <Specimen title="Radio" note="Compact options and selectable cards">
        <div className="grid gap-6 lg:grid-cols-[14rem_1fr]">
          <RadioGroup defaultValue="medium" aria-label="Difficulty">
            <RadioGroupItem value="easy" label="Easy" />
            <RadioGroupItem value="medium" label="Medium" description="Default for most rounds" />
            <RadioGroupItem value="hard" label="Hard" />
          </RadioGroup>
          <RadioGroup value={round} onValueChange={setRound} aria-label="Interview round" className="gap-2.5">
            <RadioCard
              value="technical"
              icon={<Workflow />}
              title="Technical screening"
              description="Architecture, debugging and the systems on your resume."
              aside={
                <span className="flex items-center gap-1">
                  <Clock3 className="size-3.5" aria-hidden="true" />
                  30 min
                </span>
              }
            />
            <RadioCard
              value="coding"
              icon={<Code2 />}
              title="Coding"
              description="One problem in a shared editor, in the language you choose."
              aside={
                <span className="flex items-center gap-1">
                  <Clock3 className="size-3.5" aria-hidden="true" />
                  45 min
                </span>
              }
            />
            <RadioCard
              value="behavioral"
              icon={<MessagesSquare />}
              title="Behavioral"
              description="Real situations: ownership, conflict and collaboration."
              aside={
                <span className="flex items-center gap-1">
                  <Clock3 className="size-3.5" aria-hidden="true" />
                  25 min
                </span>
              }
            />
          </RadioGroup>
        </div>
      </Specimen>

      <Specimen title="File upload" note="Drag and drop or browse; type and size checked before upload">
        <div className="max-w-xl">
          <UploadDemo />
        </div>
      </Specimen>
    </Section>
  );
}
