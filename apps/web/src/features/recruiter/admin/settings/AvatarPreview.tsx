/**
 * Live preview of the interviewer presence (lazy: pulls in three.js only when
 * shown). When a voice sample plays, its audio drives the preview through the
 * controller's analyser binding, and the clip is announced as speech so the
 * presence holds its speaking state for exactly as long as the sample plays.
 */

import { useEffect, useState } from "react";

import { AvatarStage, createAvatarController, resolveAvatarModel, type AvatarController } from "@/features/avatar";
import type { AvatarSettings } from "@/lib/types";

export interface AvatarPreviewProps {
  settings: AvatarSettings;
  /** Audio element currently playing a voice sample (drives the speaking reaction). */
  audio: HTMLAudioElement | null;
}

export default function AvatarPreview({ settings, audio }: AvatarPreviewProps) {
  const [controller, setController] = useState<AvatarController | null>(null);

  useEffect(() => {
    const c = createAvatarController({
      ...resolveAvatarModel(settings.model_format, settings.model_url),
      interviewerName: settings.interviewer_name || "Interviewer",
      background: (["studio", "office", "plain"] as const).find((b) => b === settings.background) ?? "studio",
    });
    setController(c);
    return () => c.dispose();
  }, [settings.model_url, settings.model_format, settings.interviewer_name, settings.background]);

  useEffect(() => {
    if (!controller || !audio) return;
    let context: AudioContext | null = null;
    let serial = 0;
    const announce = () => {
      if (!context) return;
      const seconds = Number.isFinite(audio.duration) ? Math.max(0, audio.duration - audio.currentTime) : 6;
      // An empty viseme track: the presence follows the analyser's loudness, the cue keeps it speaking.
      controller.enqueueSpeech({ utteranceId: `voice-preview-${++serial}`, chunkId: 0, startTime: context.currentTime, durationMs: seconds * 1000, visemes: [], text: "" });
      controller.setState("SPEAKING");
    };
    try {
      context = new AudioContext();
      const source = context.createMediaElementSource(audio);
      const analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      analyser.connect(context.destination);
      controller.attachAudio(context, analyser);
      if (!audio.paused) announce();
    } catch {
      // The preview reaction is a nicety; the sample still plays.
    }
    const onPlay = () => announce();
    const onEnded = () => controller.setState("IDLE");
    // Stopped part-way: settle at once, as a barge-in would.
    const onPause = () => {
      if (audio.ended) return;
      controller.interrupt();
      controller.setState("IDLE");
    };
    audio.addEventListener("playing", onPlay);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("pause", onPause);
    return () => {
      audio.removeEventListener("playing", onPlay);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("pause", onPause);
      controller.setState("IDLE");
      void context?.close();
    };
  }, [controller, audio]);

  if (!controller) return <div className="size-full" />;
  return <AvatarStage controller={controller} className="size-full" framing="bust" />;
}
