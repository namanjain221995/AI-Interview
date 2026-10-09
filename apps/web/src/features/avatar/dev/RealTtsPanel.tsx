/**
 * Playground panel: real TTS lip-sync. Plays captured Kokoro utterances
 * through the interview room's playback path (see realTts.ts) and scores the
 * frames the renderer produced. Exposes window.__avatarTts for scripts.
 */
import { useEffect, useRef, useState } from "react";
import type { AvatarController } from "../types";
import type { LipSyncReport } from "./lipSyncMetrics";
import { Btn, Group } from "./PlaygroundControls";
import { RealTtsPlayer, type RealTtsOptions, type RealTtsResult } from "./realTts";
import { loadTtsFixtureIndex, TTS_FIXTURES_AVAILABLE, type TtsFixtureIndexEntry } from "./ttsFixtures";

declare global {
  interface Window {
    __avatarTts?: { play(id: string, options?: RealTtsOptions): Promise<RealTtsResult>; interrupt(): void };
  }
}

function ReportTable({ report, latencyMs, longestFrameMs }: { report: LipSyncReport; latencyMs: number; longestFrameMs: number }) {
  const rows: Array<[string, string, boolean]> = [
    ["Lips sealed on p/b/m", `${report.closures.sealed} of ${report.closures.total}`, report.closures.sealed === report.closures.total],
    ["Lip-teeth contact on f/v", `${report.labiodentals.reached} of ${report.labiodentals.total}`, report.labiodentals.reached === report.labiodentals.total],
    ["Mouth at rest in pauses", `${report.pauses.closed} of ${report.pauses.total}`, report.pauses.closed === report.pauses.total],
    ["Micro-jitter", `${report.jitter.microReversalsPerSecond.toFixed(1)} per second`, report.jitter.microReversalsPerSecond < 5],
    ["Mouth vs audio", report.sync.lagMs === null ? "n/a" : `${report.sync.lagMs > 0 ? "+" : ""}${report.sync.lagMs} ms`, report.sync.lagMs === null || Math.abs(report.sync.lagMs) <= 40],
    ["Output latency compensated", `${latencyMs} ms`, true],
    // A long render stall hides the visemes inside it; failures then are not lip-sync errors.
    ["Longest frame", `${longestFrameMs} ms`, longestFrameMs < 50],
  ];
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 rounded-md border border-slate-700/60 bg-slate-950/50 p-2 text-xs" data-testid="tts-report">
      {rows.map(([label, value, ok]) => (
        <div key={label} className="contents">
          <dt className="text-slate-400">{label}</dt>
          <dd className={`text-right font-mono ${ok ? "text-slate-100" : "text-amber-200"}`}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function RealTtsPanel({ controller, muted }: { controller: AvatarController | null; muted: boolean }) {
  const [fixtures, setFixtures] = useState<TtsFixtureIndexEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState("transition");
  const [underrun, setUnderrun] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [result, setResult] = useState<RealTtsResult | null>(null);
  const playerRef = useRef<RealTtsPlayer | null>(null);

  useEffect(() => {
    if (!TTS_FIXTURES_AVAILABLE) return;
    const abort = new AbortController();
    loadTtsFixtureIndex(abort.signal).then(setFixtures, (err: unknown) => {
      if (!abort.signal.aborted) setError(err instanceof Error ? err.message : String(err));
    });
    return () => abort.abort();
  }, []);

  useEffect(() => {
    if (!controller) return;
    const player = new RealTtsPlayer(controller);
    playerRef.current = player;
    window.__avatarTts = { play: (id, options) => player.play(id, options), interrupt: () => player.interrupt() };
    return () => {
      player.dispose();
      playerRef.current = null;
      delete window.__avatarTts;
    };
  }, [controller]);

  useEffect(() => playerRef.current?.setMuted(muted), [muted, controller]);

  if (!TTS_FIXTURES_AVAILABLE) {
    return (
      <Group title="Real TTS lip-sync">
        <p className="text-xs text-slate-400">The captured TTS utterances are served by the Vite dev server only.</p>
      </Group>
    );
  }

  const play = () => {
    const player = playerRef.current;
    if (!player) return;
    setPlaying(true);
    setError(null);
    player.play(selected, { underrun }).then(
      (r) => {
        setResult(r);
        setPlaying(false);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
        setPlaying(false);
      },
    );
  };

  const current = fixtures?.find((f) => f.id === selected);
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-medium text-slate-400">Real TTS lip-sync</h3>
      <div className="flex flex-wrap gap-1.5">
        {(fixtures ?? []).map((f) => (
          <Btn key={f.id} testId={`tts-${f.id}`} active={selected === f.id} onClick={() => setSelected(f.id)}>
            {f.title}
          </Btn>
        ))}
      </div>
      {current && (
        <p className="text-xs leading-relaxed text-slate-300">
          “{current.text}” <span className="text-slate-500">({(current.duration_ms / 1000).toFixed(1)} s, {current.chunks} chunks)</span>
        </p>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Btn testId="tts-play" disabled={!fixtures || playing} onClick={play}>
          {playing ? "Playing…" : "Play through the room's playback"}
        </Btn>
        <Btn testId="tts-interrupt" disabled={!playing} onClick={() => playerRef.current?.interrupt()}>
          Barge in
        </Btn>
        <Btn testId="tts-underrun" active={underrun} onClick={() => setUnderrun((u) => !u)}>
          Late last chunk
        </Btn>
      </div>
      {error && <p className="text-xs text-red-300">{error}</p>}
      {result && <ReportTable report={result.report} latencyMs={result.outputLatencyMs} longestFrameMs={result.longestFrameMs} />}
    </section>
  );
}
