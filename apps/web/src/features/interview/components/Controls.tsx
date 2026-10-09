import { Check, Keyboard, LogOut, Mic, MicOff, RotateCcw, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Kbd } from "@/components/ui/Kbd";
import { Textarea } from "@/components/ui/Textarea";
import { activityDockMode } from "@/features/intake/candidate/room/activityModel";
import { createInterviewStore, useInterviewState, type InterviewStore } from "@/features/realtime/store";
import { cn } from "@/lib/utils";

import { useRuntimeMedia } from "../hooks";
import type { InterviewRuntime } from "../runtime";
import { MicMeter } from "./Stage";

export function ControlDock({
  store,
  runtime,
  onOpenTyping,
  onEnd,
  typingOpen,
}: {
  store: InterviewStore;
  runtime: InterviewRuntime | null;
  onOpenTyping: () => void;
  onEnd: () => void;
  typingOpen: boolean;
}) {
  const turn = useInterviewState(store, (s) => s.session?.turnState ?? "idle");
  const allowTyped = useInterviewState(store, (s) => s.session?.allowTypedAnswers ?? true);
  const connected = useInterviewState(store, (s) => s.connection.status === "connected");
  const ended = useInterviewState(store, (s) => Boolean(s.ended));
  const begun = useInterviewState(store, (s) => s.begun);
  const preparing = useInterviewState(store, (s) => s.preparing);
  const coding = useInterviewState(store, (s) => Boolean(s.coding && !s.coding.ended));
  const asked = useInterviewState(store, (s) => Boolean(s.lastInterviewerText.trim()));
  // Intake reading/speaking exercises: the panel owns the finish action; typing only where an answer is spoken.
  const activityMode = useInterviewState(store, (s) => activityDockMode(s.activity));
  const inActivity = activityMode !== "none";
  const [flash, setFlash] = useState<"done" | "repeat" | null>(null);
  const { microphone: micOn } = useRuntimeMedia(runtime);
  const yourTurn = turn === "listening" || turn === "candidate_speaking";
  const live = connected && !ended && begun && !preparing;
  // In the coding exercise the floor is open: "Done speaking" only ends something you said.
  const canFinish = !inActivity && live && (coding ? turn === "candidate_speaking" : yourTurn);
  const canRepeat = live && asked && (inActivity ? activityMode === "listening" : yourTurn || turn === "coding");
  const canType = live && turn !== "idle" && turn !== "ended" && (activityMode === "none" || activityMode === "capture");

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 1600);
    return () => clearTimeout(t);
  }, [flash]);

  const doneLabel = coding ? "Done speaking" : "Done answering";

  return (
    <div className="border-t border-line-subtle bg-surface-sunken/85 px-3 pt-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] backdrop-blur-md sm:px-6 sm:py-3">
      <div className="mx-auto grid max-w-6xl grid-cols-[1fr_1fr_auto] items-center gap-2 sm:flex sm:gap-2.5">
        <div className="hidden min-w-0 items-center gap-2.5 text-sm text-fg-muted sm:flex sm:flex-1">
          {micOn ? (
            <Mic className="size-4 shrink-0 text-signal" aria-hidden="true" />
          ) : (
            <MicOff className={cn("size-4 shrink-0", begun ? "text-warning-fg" : "text-fg-subtle")} aria-hidden="true" />
          )}
          <span className="truncate">{micOn ? "Microphone on" : begun ? "Microphone off" : "Microphone starts when you begin"}</span>
          {micOn ? <MicMeter runtime={runtime} surface="panel" /> : null}
        </div>

        {inActivity ? null : (
          <Button
            size="lg"
            variant={canFinish ? "primary" : "secondary"}
            leftIcon={<Check />}
            disabled={!canFinish}
            onClick={() => {
              runtime?.endTurn();
              setFlash("done");
            }}
            aria-label={doneLabel}
            className="col-span-3 h-11 sm:h-10"
            data-testid="done-answering-button"
          >
            {flash === "done" ? "Got it" : doneLabel}
          </Button>
        )}
        <Button
          size="lg"
          variant="secondary"
          leftIcon={<RotateCcw />}
          disabled={!canRepeat}
          onClick={() => {
            runtime?.repeat();
            setFlash("repeat");
          }}
          aria-label={inActivity ? "Repeat instructions" : "Repeat question"}
          className="max-sm:h-9 max-sm:px-3 max-sm:text-sm"
          data-testid="repeat-question-button"
        >
          {flash === "repeat" ? (
            "Repeating"
          ) : (
            <>
              <span className="sm:hidden">Repeat</span>
              <span className="hidden sm:inline">{inActivity ? "Repeat instructions" : "Repeat question"}</span>
            </>
          )}
        </Button>
        {allowTyped ? (
          <Button
            size="lg"
            variant="secondary"
            leftIcon={<Keyboard />}
            aria-expanded={typingOpen}
            aria-label="Type answer"
            disabled={!canType && !typingOpen}
            onClick={onOpenTyping}
            className="max-sm:h-9 max-sm:px-3 max-sm:text-sm"
            data-testid="type-answer-button"
          >
            <span className="sm:hidden">Type</span>
            <span className="hidden sm:inline">Type answer</span>
          </Button>
        ) : (
          <span className="sm:hidden" />
        )}

        {/* Without "Done answering" (an intake exercise) the dock fits one row on phones. */}
        <div className={cn("col-start-3 flex justify-end sm:flex-1", inActivity ? "row-start-1" : "row-start-2")}>
          <Button
            variant="ghost"
            leftIcon={<LogOut />}
            onClick={onEnd}
            disabled={ended || !begun}
            aria-label="End interview"
            className="text-fg-muted hover:text-danger-fg max-sm:h-9 max-sm:px-2.5"
            data-testid="end-interview-button"
          >
            <span className="sm:hidden">End</span>
            <span className="hidden sm:inline">End interview</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

export function TypedAnswerPanel({ runtime, store, onClose, className }: { runtime: InterviewRuntime | null; store?: InterviewStore; onClose: () => void; className?: string }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const question = useOptionalQuestion(store);
  useEffect(() => ref.current?.focus(), []);
  const submit = () => {
    if (runtime?.sendTypedAnswer(text)) {
      setText("");
      onClose();
    }
  };
  return (
    <div className={cn("rounded-xl border border-line bg-surface-raised p-3 shadow-overlay", className)} role="group" aria-label="Type your answer">
      {question ? (
        <p className="mb-2.5 line-clamp-3 px-0.5 text-[13px] leading-5 text-fg-muted">
          <span className="font-medium text-fg">Question: </span>
          {question}
        </p>
      ) : null}
      <Textarea
        ref={ref}
        data-testid="typed-answer-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            submit();
          } else if (e.key === "Escape") {
            onClose();
          }
        }}
        autoResize
        rows={3}
        maxRows={8}
        maxLength={8000}
        placeholder="Type your answer…"
        aria-label="Your answer"
      />
      <div className="mt-2 flex items-center justify-between gap-3">
        <p className="hidden text-xs text-fg-subtle sm:block">
          <Kbd>Ctrl</Kbd> + <Kbd>Enter</Kbd> to send
        </p>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" leftIcon={<Send />} disabled={!text.trim()} onClick={submit} data-testid="typed-answer-submit">
            Send answer
          </Button>
        </div>
      </div>
    </div>
  );
}

/** The question being answered (the panel also works without a store, e.g. in tests). */
function useOptionalQuestion(store: InterviewStore | undefined): string {
  const fallback = useMemo(() => createInterviewStore(), []);
  return useInterviewState(store ?? fallback, (s) => s.lastInterviewerText.trim());
}

export function EndInterviewDialog({ open, onOpenChange, onConfirm }: { open: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm" hideClose>
        <DialogHeader>
          <DialogTitle>End the interview now?</DialogTitle>
          <DialogDescription>Your answers so far are saved and will be reviewed by the hiring team. You won't be able to rejoin after ending.</DialogDescription>
        </DialogHeader>
        <DialogFooter className="mt-5">
          <Button variant="outline" onClick={() => onOpenChange(false)} autoFocus>
            Keep going
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
            data-testid="end-interview-confirm"
          >
            End interview
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
