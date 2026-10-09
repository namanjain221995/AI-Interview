/**
 * Avatar playground (dev tool): mounted at /dev/avatar by the app router and
 * standalone at /avatar-dev.html. Exercises every state, expression, gesture,
 * intensity and tone, lip-sync (real Kokoro TTS utterances played through the
 * interview room's playback path, synthetic oscillator speech, or browser
 * speechSynthesis word boundaries), barge-in, candidate speech with
 * backchannel nods, render-mode switching and context loss.
 *
 * URL parameters: ?model=aurora|sunrise|ocean|glb|procedural|2d|none (the
 * first three are presence orb palettes; "2d" is the orb's 2D fallback),
 * ?stage=light (light backdrop), ?size=compact (coding-corner size),
 * ?framing=wide, ?dpr=1.5 (pins the pixel ratio), ?clock=manual (the avatar
 * only moves when window.__avatarClock.set(t) is called; with
 * window.__avatarStrip this gives frame-exact lip-sync captures; add
 * ?latency=0.04 to simulate a speaker output latency in those captures).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AvatarExpression,
  AvatarGesture,
  AvatarState,
  GestureIntensity,
  SpeechTone,
  Viseme,
} from "@/lib/realtime/protocol";
import { AvatarStage } from "./AvatarStage";
import { createAvatarController } from "./controller";
import { Btn, Chip, Group } from "./dev/PlaygroundControls";
import { RealTtsPanel } from "./dev/RealTtsPanel";
import { createManualClock, createStripApi, type ManualClock, type StripApi } from "./dev/realTts";
import { estimateWordDurationMs, textToVisemes } from "./lipsync/visemes";
import type { AvatarConfig, AvatarController, AvatarStatus } from "./types";

const STATES: AvatarState[] = [
  "IDLE",
  "LISTENING",
  "ACKNOWLEDGING",
  "THINKING",
  "SPEAKING",
  "QUESTIONING",
  "ENCOURAGING",
  "TRANSITION",
  "INTERRUPTED",
  "CLOSING",
];
const EXPRESSIONS: AvatarExpression[] = ["neutral", "warm", "curious", "thoughtful", "encouraging", "serious", "pleased"];
const GESTURES: AvatarGesture[] = [
  "none",
  "small_open_palm",
  "one_hand_explain",
  "two_hand_explain",
  "small_emphasis",
  "question_gesture",
  "nod",
  "head_tilt",
  "goodbye",
];
const INTENSITIES: GestureIntensity[] = ["low", "medium", "high"];
const TONES: SpeechTone[] = ["professional", "friendly", "supportive", "neutral"];

type ModelChoice = "aurora" | "sunrise" | "ocean" | "glb" | "procedural" | "2d" | "none";
const MODEL_CHOICES: ModelChoice[] = ["aurora", "sunrise", "ocean", "glb", "procedural", "2d", "none"];
const MODEL_LABELS: Record<ModelChoice, string> = {
  aurora: "Orb Aurora",
  sunrise: "Orb Sunrise",
  ocean: "Orb Ocean",
  glb: "3D avatar (GLB)",
  procedural: "Procedural",
  "2d": "2D",
  none: "None",
};

const SAMPLE_TEXT =
  "Thanks for joining today. Could you walk me through a recent project where you had to balance speed and quality, and what you learned from it?";

const PARAMS = new URLSearchParams(typeof location !== "undefined" ? location.search : "");

/** ?dpr=1.5 pins the pixel ratio (no adaptive quality), useful for screenshots. */
const FIXED_DPR = (() => {
  const v = Number(PARAMS.get("dpr"));
  return Number.isFinite(v) && v > 0 ? v : null;
})();

/** ?clock=manual: deterministic time for frame-exact captures. */
const MANUAL_CLOCK: ManualClock | null = PARAMS.get("clock") === "manual" ? createManualClock(1) : null;

declare global {
  interface Window {
    __avatar?: AvatarController;
    __avatarClock?: ManualClock;
    __avatarStrip?: StripApi;
  }
}

/** ?palette= picks the orb palette used by the 2D / none modes. */
const PALETTE_PARAM = PARAMS.get("palette");
const FALLBACK_PALETTE = PALETTE_PARAM === "sunrise" || PALETTE_PARAM === "ocean" ? PALETTE_PARAM : "aurora";

function modelFor(model: ModelChoice): Pick<AvatarConfig, "modelUrl" | "modelFormat"> {
  if (model === "procedural") return { modelUrl: "procedural", modelFormat: "procedural" };
  if (model === "glb") return { modelUrl: "/avatars/interviewer.glb", modelFormat: "glb" };
  const palette = model === "aurora" || model === "sunrise" || model === "ocean" ? model : FALLBACK_PALETTE;
  return { modelUrl: `orb:${palette}`, modelFormat: "orb" };
}

function configFor(model: ModelChoice, background: AvatarConfig["background"], reducedMotion: boolean): AvatarConfig {
  return {
    ...(FIXED_DPR ? { maxDpr: FIXED_DPR } : {}),
    ...modelFor(model),
    interviewerName: "Maya",
    background,
    reducedMotion,
    renderMode: model === "2d" ? "2d" : model === "none" ? "none" : "auto",
    seed: 7,
  };
}

interface AudioRig {
  ctx: AudioContext;
  analyser: AnalyserNode;
  out: GainNode;
}

/**
 * Synthetic speech: a filtered sawtooth whose loudness follows a viseme
 * timeline generated from text, scheduled on the AudioContext clock and
 * delivered to the controller in ~2 s chunks like server TTS.
 */
function playSynthetic(rig: AudioRig, controller: AvatarController, text: string): number {
  const { ctx, analyser } = rig;
  const words = text.split(/\s+/).filter(Boolean);
  const startAt = ctx.currentTime + 0.12;
  const osc = ctx.createOscillator();
  osc.type = "sawtooth";
  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = 1100;
  const gain = ctx.createGain();
  gain.gain.value = 0;
  osc.connect(filter).connect(gain).connect(analyser);

  let chunk: Viseme[] = [];
  let chunkStart = 0;
  let chunkText: string[] = [];
  let chunkId = 0;
  let t = 0;
  const utteranceId = `demo-${Date.now()}`;
  const flush = () => {
    if (!chunk.length) return;
    controller.enqueueSpeech({
      utteranceId,
      chunkId: chunkId++,
      startTime: startAt + chunkStart / 1000,
      durationMs: Math.round(t - chunkStart),
      visemes: chunk.map((v) => ({ ...v, t: Math.round(v.t - chunkStart) })),
      text: chunkText.join(" "),
    });
    chunk = [];
    chunkText = [];
    chunkStart = t;
  };
  for (const word of words) {
    const steps = textToVisemes(word);
    const duration = estimateWordDurationMs(word);
    const total = steps.reduce((n, s) => n + s.d, 0) || 1;
    for (const s of steps) {
      const vowel = s.v === "aa" || s.v === "E" || s.v === "I" || s.v === "O" || s.v === "U";
      chunk.push({ t, v: s.v, w: s.w });
      const at = startAt + t / 1000;
      gain.gain.setTargetAtTime(vowel ? 0.05 : s.v === "PP" ? 0.004 : 0.02, at, 0.018);
      osc.frequency.setTargetAtTime(vowel ? 175 + 25 * Math.sin(t / 310) : 150, at, 0.04);
      t += (s.d / total) * duration;
    }
    chunkText.push(word);
    const pause = /[.,?!;:]$/.test(word) ? 260 : 55;
    chunk.push({ t, v: "sil", w: 0 });
    gain.gain.setTargetAtTime(0, startAt + t / 1000, 0.02);
    t += pause;
    if (t - chunkStart > 2000) flush();
  }
  flush();
  osc.start(startAt);
  osc.stop(startAt + t / 1000 + 0.3);
  return t;
}

export default function AvatarPlayground() {
  const [model, setModel] = useState<ModelChoice>(() => {
    const q = PARAMS.get("model") as ModelChoice | null;
    return q && MODEL_CHOICES.includes(q) ? q : "aurora";
  });
  const [background, setBackground] = useState<NonNullable<AvatarConfig["background"]>>("studio");
  const [lightStage, setLightStage] = useState(() => PARAMS.get("stage") === "light");
  const [compact, setCompact] = useState(() => PARAMS.get("size") === "compact");
  const [dimmed, setDimmed] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const config = useMemo(() => configFor(model, background, reducedMotion), [model, background, reducedMotion]);
  // Created in an effect so React StrictMode's mount/unmount/mount cycle disposes
  // the first instance and keeps a live second one (the pattern to use in the app).
  const [controller, setController] = useState<AvatarController | null>(null);
  const [status, setStatus] = useState<AvatarStatus>({ mode: "3d", loaded: false, fps: 0 });
  const [state, setStateName] = useState<AvatarState>("IDLE");
  const [expression, setExpression] = useState<AvatarExpression>("neutral");
  const [intensity, setIntensity] = useState<GestureIntensity>("medium");
  const [tone, setTone] = useState<SpeechTone>("professional");
  const [text, setText] = useState(SAMPLE_TEXT);
  const [muted, setMuted] = useState(true);
  const [candidate, setCandidate] = useState(false);
  const [wide, setWide] = useState(() => PARAMS.get("framing") === "wide");
  const audioRef = useRef<AudioRig | null>(null);

  useEffect(() => {
    const c = createAvatarController(configFor("aurora", "studio", false), MANUAL_CLOCK ? { wallClock: MANUAL_CLOCK.now } : {});
    const unsubscribe = c.subscribe(setStatus);
    setStatus(c.getStatus());
    setController(c);
    window.__avatar = c;
    if (MANUAL_CLOCK) {
      window.__avatarClock = MANUAL_CLOCK;
      const latency = Number(PARAMS.get("latency"));
      window.__avatarStrip = createStripApi(c, MANUAL_CLOCK, { outputLatency: Number.isFinite(latency) && latency > 0 ? latency : 0 });
    }
    return () => {
      unsubscribe();
      c.dispose();
      audioRef.current = null;
      delete window.__avatarStrip;
    };
  }, []);

  const ensureAudio = useCallback((): AudioRig | null => {
    if (!controller) return null;
    if (audioRef.current) {
      void audioRef.current.ctx.resume();
      return audioRef.current;
    }
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    const out = ctx.createGain();
    out.gain.value = muted ? 0 : 0.6;
    analyser.connect(out).connect(ctx.destination);
    audioRef.current = { ctx, analyser, out };
    controller.attachAudio(ctx, analyser);
    return audioRef.current;
  }, [controller, muted]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.out.gain.value = muted ? 0 : 0.6;
  }, [muted]);

  useEffect(() => () => void audioRef.current?.ctx.close(), []);

  // Simulated candidate speech: syllabic level with natural pauses.
  useEffect(() => {
    if (!controller) return;
    if (!candidate) {
      controller.setCandidateSpeaking(false, 0);
      return;
    }
    controller.setState("LISTENING");
    setStateName("LISTENING");
    const started = performance.now();
    const id = window.setInterval(() => {
      const t = (performance.now() - started) / 1000;
      const phrase = t % 3.2;
      const level = phrase < 2.4 ? 0.25 + 0.2 * Math.abs(Math.sin(t * 9)) : 0.01;
      controller.setCandidateSpeaking(true, level);
    }, 80);
    return () => window.clearInterval(id);
  }, [candidate, controller]);

  // State buttons send only the state (each state has its own default expression);
  // expression and gesture buttons refine the current state.
  const apply = (patch: { state?: AvatarState; expression?: AvatarExpression; gesture?: AvatarGesture }) => {
    if (!controller) return;
    const next = patch.state ?? state;
    controller.applyBehavior({
      state: next,
      ...(patch.expression ? { expression: patch.expression } : {}),
      gesture: patch.gesture ?? "none",
      gesture_intensity: intensity,
      tone,
    });
    setStateName(next);
    if (patch.expression) setExpression(patch.expression);
  };

  const speakSynthetic = () => {
    const rig = ensureAudio();
    if (!rig || !controller) return;
    controller.applyBehavior({ state: "SPEAKING", gesture: "one_hand_explain", gesture_intensity: intensity, tone });
    setStateName("SPEAKING");
    playSynthetic(rig, controller, text);
  };

  const speakBrowser = () => {
    if (typeof speechSynthesis === "undefined" || !controller) return;
    ensureAudio();
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1;
    u.onstart = () => {
      controller.applyBehavior({ state: "SPEAKING", gesture: "small_open_palm", gesture_intensity: intensity, tone });
      setStateName("SPEAKING");
    };
    u.onboundary = (e) => {
      if (e.name !== "word") return;
      const word = text.slice(e.charIndex).split(/\s+/)[0] ?? "";
      controller.speakWordBoundary(word, estimateWordDurationMs(word));
    };
    u.onend = () => {
      controller.setState("LISTENING");
      setStateName("LISTENING");
    };
    speechSynthesis.speak(u);
  };

  const interrupt = () => {
    controller?.interrupt();
    if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
    setStateName("INTERRUPTED");
  };

  const loseContext = () => {
    const canvas = document.querySelector<HTMLCanvasElement>("[data-avatar-stage] canvas");
    const gl = canvas?.getContext("webgl2") ?? canvas?.getContext("webgl");
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  };

  const fpsTone = status.fps >= 50 ? "green" : status.fps >= 30 ? "amber" : "red";
  return (
    <div className="theme-stage min-h-full bg-[#0b0f1e] text-slate-100">
      <div className="mx-auto flex max-w-[1500px] flex-col gap-4 p-4 lg:flex-row">
        <div className="flex-1 space-y-3">
          <header className="flex flex-wrap items-center gap-2">
            <h1 className="mr-2 text-base font-semibold tracking-tight">Avatar playground</h1>
            <Chip>mode {status.mode}</Chip>
            <Chip tone={status.loaded ? "green" : "amber"}>{status.loaded ? "loaded" : "loading"}</Chip>
            <Chip tone={fpsTone} >
              <span data-testid="fps">{status.fps}</span> fps
            </Chip>
            {status.dpr !== undefined && <Chip>dpr {status.dpr}</Chip>}
            {status.modelName && <Chip>{status.modelName}</Chip>}
            <Chip>state {state}</Chip>
            {status.error && <Chip tone="red">{status.error}</Chip>}
          </header>
          <div
            className={`relative overflow-hidden rounded-xl border shadow-2xl ${compact ? "aspect-[4/3] w-[200px]" : "aspect-video w-full"} ${
              lightStage
                ? "border-slate-300 bg-[radial-gradient(80%_70%_at_50%_30%,#ffffff,#e9eef6)]"
                : "border-slate-700/60 bg-[radial-gradient(80%_70%_at_50%_30%,var(--surface-raised),var(--surface-sunken))]"
            }`}
            data-avatar-stage
          >
            {controller && (
              <AvatarStage
                controller={controller}
                config={config}
                framing={wide ? "upper-body" : "bust"}
                adaptiveQuality={FIXED_DPR === null}
                dimmed={dimmed}
                className="h-full w-full"
              />
            )}
          </div>
        </div>
        <aside className="w-full space-y-4 rounded-xl border border-slate-700/60 bg-slate-900/60 p-4 lg:w-[400px]">
          <Group title="Model">
            {MODEL_CHOICES.map((m) => (
              <Btn key={m} testId={`model-${m}`} active={model === m} onClick={() => setModel(m)}>
                {MODEL_LABELS[m]}
              </Btn>
            ))}
            <span className="mx-1 w-px bg-slate-700" />
            {(["studio", "office", "plain"] as const).map((b) => (
              <Btn key={b} testId={`bg-${b}`} active={background === b} onClick={() => setBackground(b)}>
                {b}
              </Btn>
            ))}
          </Group>
          <Group title="State">
            {STATES.map((s) => (
              <Btn key={s} testId={`state-${s}`} active={state === s} onClick={() => apply({ state: s })}>
                {s.toLowerCase()}
              </Btn>
            ))}
          </Group>
          <Group title="Expression">
            {EXPRESSIONS.map((e) => (
              <Btn key={e} testId={`expr-${e}`} active={expression === e} onClick={() => apply({ expression: e })}>
                {e}
              </Btn>
            ))}
          </Group>
          <Group title="Gesture">
            {GESTURES.map((g) => (
              <Btn key={g} testId={`gesture-${g}`} onClick={() => apply({ gesture: g })}>
                {g.replace(/_/g, " ")}
              </Btn>
            ))}
          </Group>
          <Group title="Intensity / tone">
            {INTENSITIES.map((i) => (
              <Btn key={i} testId={`intensity-${i}`} active={intensity === i} onClick={() => setIntensity(i)}>
                {i}
              </Btn>
            ))}
            <span className="mx-1 w-px bg-slate-700" />
            {TONES.map((t) => (
              <Btn key={t} testId={`tone-${t}`} active={tone === t} onClick={() => setTone(t)}>
                {t}
              </Btn>
            ))}
          </Group>
          <RealTtsPanel controller={controller} muted={muted} />
          <section className="space-y-2">
            <h3 className="text-xs font-medium text-slate-400">Synthetic speech</h3>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              className="w-full resize-none rounded-md border border-slate-600/60 bg-slate-950/70 p-2 text-xs text-slate-100 outline-none focus:border-cyan-400/60"
            />
            <div className="flex flex-wrap gap-1.5">
              <Btn testId="speak-synthetic" onClick={speakSynthetic}>
                Speak (synthetic visemes)
              </Btn>
              <Btn testId="speak-browser" onClick={speakBrowser}>
                Speak (speechSynthesis)
              </Btn>
              <Btn testId="interrupt" onClick={interrupt}>
                Interrupt
              </Btn>
              <Btn testId="mute" active={!muted} onClick={() => setMuted((m) => !m)}>
                {muted ? "Sound off" : "Sound on"}
              </Btn>
            </div>
          </section>
          <Group title="Simulation">
            <Btn testId="candidate" active={candidate} onClick={() => setCandidate((c) => !c)}>
              Candidate speaking
            </Btn>
            <Btn testId="reduced-motion" active={reducedMotion} onClick={() => setReducedMotion((r) => !r)}>
              Reduced motion
            </Btn>
            <Btn testId="framing" active={wide} onClick={() => setWide((w) => !w)}>
              Wide framing
            </Btn>
            <Btn testId="light-stage" active={lightStage} onClick={() => setLightStage((v) => !v)}>
              Light stage
            </Btn>
            <Btn testId="compact" active={compact} onClick={() => setCompact((v) => !v)}>
              Compact
            </Btn>
            <Btn testId="dimmed" active={dimmed} onClick={() => setDimmed((v) => !v)}>
              Paused / offline
            </Btn>
            <Btn testId="lose-context" onClick={loseContext}>
              Lose WebGL context
            </Btn>
          </Group>
          <p className="break-all text-[11px] leading-relaxed text-slate-500">
            Status: <span className="font-mono">{JSON.stringify(status)}</span>
          </p>
        </aside>
      </div>
    </div>
  );
}
