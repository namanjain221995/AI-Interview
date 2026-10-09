import { Pause, Play } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Combobox } from "@/components/ui/Combobox";
import { Input } from "@/components/ui/Input";
import { RadioCard, RadioGroup } from "@/components/ui/RadioGroup";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { ORB_PALETTE_SPECS, ORB_PALETTES, orbModelUrl, parseOrbModel, type OrbPalette } from "@/features/avatar";
import type { AvatarSettings } from "@/lib/types";

import { fetchVoicePreview, useVoices, VoicePreviewUnavailable } from "../api/admin";
import { FieldShell } from "../components/Field";
import { describeFailure } from "../lib/serverErrors";
import { REALISTIC_AVATAR_URL } from "./settingsModel";
import { voiceDisplayName, VOICE_SAMPLE_TEXT } from "./voices";

const AvatarPreview = lazy(() => import("./AvatarPreview"));

export interface InterviewerSettingsProps {
  value: AvatarSettings;
  onChange: (value: AvatarSettings) => void;
  errors: Record<string, string>;
  disabled?: boolean;
}

/** A small static rendition of a presence palette (for the choice cards). */
function OrbSwatch({ palette }: { palette: OrbPalette }) {
  const p = ORB_PALETTE_SPECS[palette];
  return (
    <span
      aria-hidden="true"
      className="block size-9 shrink-0 rounded-full"
      style={{
        background: `radial-gradient(circle at 34% 30%, ${p.flow[3]} 0%, ${p.flow[2]} 34%, ${p.flow[1]} 66%, ${p.flow[0]} 100%)`,
        boxShadow: `0 0 14px 1px ${p.glow}66, inset 0 0 6px 1px ${p.rim}99`,
      }}
    />
  );
}

const THREE_D = "3d";

/** Interviewer name, voice (with a spoken sample) and presence (with a live preview). */
export function InterviewerSettings({ value, onChange, errors, disabled }: InterviewerSettingsProps) {
  const voices = useVoices();
  const [audio, setAudio] = useState<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [loadingSample, setLoadingSample] = useState(false);
  const [previewNote, setPreviewNote] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const set = <K extends keyof AvatarSettings>(key: K, next: AvatarSettings[K]) => onChange({ ...value, [key]: next });
  const isOrb = value.model_format === "orb" || value.model_format === "cartoon";
  const presence = isOrb ? orbModelUrl(parseOrbModel(value.model_url)) : THREE_D;

  const audioRef = useRef<HTMLAudioElement | null>(null);
  audioRef.current = audio;
  useEffect(
    () => () => {
      abortRef.current?.abort();
      audioRef.current?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  const voiceOptions = useMemo(
    () =>
      (voices.data?.voices ?? []).map((v) => ({
        value: v.id,
        label: voiceDisplayName(v.id),
        description: `${v.language_name}, ${v.gender}${v.default ? ", default" : ""}`,
        keywords: `${v.id} ${v.language_name} ${v.gender}`,
      })),
    [voices.data],
  );

  const choosePresence = (next: string) => {
    if (next === THREE_D) {
      const keep = value.model_format === "glb" || value.model_format === "vrm" || value.model_format === "procedural";
      onChange({ ...value, model_format: keep ? value.model_format : "glb", model_url: keep ? value.model_url : REALISTIC_AVATAR_URL });
    } else {
      onChange({ ...value, model_format: "orb", model_url: next });
    }
  };

  const playSample = async () => {
    if (playing && audio) {
      audio.pause();
      setPlaying(false);
      return;
    }
    setPreviewNote(null);
    setLoadingSample(true);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const blob = await fetchVoicePreview(value.voice, `${VOICE_SAMPLE_TEXT.replace("your interviewer", value.interviewer_name || "your interviewer")}`, controller.signal);
      audio?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const element = new Audio(url);
      element.addEventListener("ended", () => setPlaying(false));
      element.addEventListener("pause", () => setPlaying(false));
      setAudio(element);
      await element.play();
      setPlaying(true);
    } catch (error) {
      if (controller.signal.aborted) return;
      setPreviewNote(error instanceof VoicePreviewUnavailable ? "Voice samples aren't available on this server yet. The voice still applies to new interviews." : describeFailure(error, "Couldn't play the sample."));
    } finally {
      setLoadingSample(false);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_17rem]">
      <div className="grid content-start gap-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <FieldShell label="Interviewer name" required error={errors["avatar.interviewer_name"]} description="How the interviewer introduces themselves.">
            {(control) => <Input {...control} value={value.interviewer_name} maxLength={40} disabled={disabled} onChange={(e) => set("interviewer_name", e.target.value)} />}
          </FieldShell>
          <FieldShell
            label="Voice"
            error={errors["avatar.voice"]}
            description={voices.data && !voices.data.live ? "Voices of the built-in speech engine." : "Interviewers speak English best."}
          >
            {(control) => (
              <Combobox {...control} options={voiceOptions} value={value.voice} clearable={false} disabled={disabled} onChange={(v) => v && set("voice", v)} placeholder="Search voices" />
            )}
          </FieldShell>
        </div>

        <FieldShell
          label="Interviewer presence"
          error={errors["avatar.model_url"]}
          description="What candidates see during the interview. The presence listens, thinks and speaks with the interviewer's voice."
        >
          {(control) => (
            <RadioGroup
              id={control.id}
              aria-describedby={control["aria-describedby"]}
              aria-invalid={control["aria-invalid"]}
              aria-label="Interviewer presence"
              value={presence}
              onValueChange={choosePresence}
              disabled={disabled}
              className="grid gap-2 sm:grid-cols-3"
            >
              {ORB_PALETTES.map((palette) => (
                <RadioCard
                  key={palette}
                  value={orbModelUrl(palette)}
                  icon={<OrbSwatch palette={palette} />}
                  title={ORB_PALETTE_SPECS[palette].label}
                  description={ORB_PALETTE_SPECS[palette].description}
                  className="p-3"
                />
              ))}
              <RadioCard
                value={THREE_D}
                title="3D avatar"
                description="A realistic 3D character instead of the presence. Needs a stronger graphics card."
                className="p-3 sm:col-span-3"
              />
            </RadioGroup>
          )}
        </FieldShell>

        {!isOrb ? (
          <div className="grid gap-4 sm:grid-cols-[10rem_1fr]">
            <FieldShell label="Avatar format">
              {(control) => (
                <Select<AvatarSettings["model_format"]>
                  id={control.id}
                  value={value.model_format}
                  disabled={disabled}
                  onValueChange={(v) => set("model_format", v)}
                  options={[
                    { value: "glb", label: "GLB model" },
                    { value: "vrm", label: "VRM model" },
                    { value: "procedural", label: "Built-in figure" },
                  ]}
                />
              )}
            </FieldShell>
            <FieldShell label="Model file" description="A file served by this site, such as /avatars/interviewer.glb.">
              {(control) => (
                <Input {...control} className="font-mono text-[13px]" value={value.model_url} disabled={disabled || value.model_format === "procedural"} onChange={(e) => set("model_url", e.target.value)} />
              )}
            </FieldShell>
            <FieldShell label="Background" className="sm:col-span-2 sm:max-w-[10rem]">
              {(control) => (
                <Select<string>
                  id={control.id}
                  value={value.background}
                  disabled={disabled}
                  onValueChange={(v) => set("background", v)}
                  options={[
                    { value: "studio", label: "Studio" },
                    { value: "office", label: "Office" },
                    { value: "plain", label: "Plain" },
                    ...(["studio", "office", "plain"].includes(value.background) ? [] : [{ value: value.background, label: value.background }]),
                  ]}
                />
              )}
            </FieldShell>
          </div>
        ) : null}
      </div>

      <div className="grid content-start gap-2">
        <div className="relative aspect-[4/5] overflow-hidden rounded-lg bg-[radial-gradient(80%_70%_at_50%_40%,var(--color-ink-800),var(--color-ink-950))] ring-1 ring-line">
          <Suspense fallback={<Skeleton className="size-full rounded-none" />}>
            <AvatarPreview settings={value} audio={audio} />
          </Suspense>
          <span className="absolute bottom-2 left-2 rounded-xs bg-black/50 px-1.5 py-0.5 text-2xs font-medium text-white">{value.interviewer_name || "Interviewer"}</span>
        </div>
        <Button variant="outline" leftIcon={playing ? <Pause /> : <Play />} loading={loadingSample} disabled={disabled && !playing} onClick={() => void playSample()}>
          {playing ? "Stop" : "Preview voice"}
        </Button>
        {previewNote ? <p className="text-xs text-fg-muted">{previewNote}</p> : <p className="text-xs text-fg-muted">The preview reacts to the sample the way candidates see it react.</p>}
      </div>
    </div>
  );
}
