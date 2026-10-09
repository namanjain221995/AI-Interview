/**
 * Real TTS lip-sync fixtures (dev only). Each fixture is one interviewer
 * utterance captured from the running Kokoro service (`POST /v1/tts`, voice
 * af_heart, speed 1.0, 24 kHz): the text was streamed through the API's own
 * SentenceChunker, so the chunks, their PCM16 audio and the model-timed
 * Oculus visemes are exactly what the realtime runtime sends as `tts.chunk` +
 * binary 0x02 frames. Metadata lives in `fixtures/<id>.json`, audio in
 * `fixtures/<id>-<index>.pcm` (mono little-endian PCM16).
 *
 * The files are served by the Vite dev server only (they are fetched by path,
 * never imported, so production builds do not ship them).
 */
import type { TtsChunkMsg, Viseme } from "@/lib/realtime/protocol";

export interface TtsFixtureChunk {
  chunk_id: number;
  index: number;
  text: string;
  sample_rate: number;
  duration_ms: number;
  /** Phonemes the TTS spoke (diagnostics). */
  phonemes: string;
  /** PCM file name next to the JSON. */
  audio: string;
  visemes: Viseme[];
}

export interface TtsFixture {
  id: string;
  title: string;
  text: string;
  voice: string;
  speed: number;
  source: { endpoint: string; captured: string };
  chunks: TtsFixtureChunk[];
}

export interface TtsFixtureIndexEntry {
  id: string;
  title: string;
  text: string;
  chunks: number;
  duration_ms: number;
}

/** Dev-server path of the fixture folder. */
export const TTS_FIXTURE_BASE = "/src/features/avatar/dev/fixtures/";

/** True when the fixtures can be fetched (Vite dev server). */
export const TTS_FIXTURES_AVAILABLE = import.meta.env.DEV;

/** Mono little-endian PCM16 bytes as samples (copies when the buffer is unaligned). */
export function pcm16FromBytes(bytes: ArrayBuffer | Uint8Array): Int16Array {
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const count = view.byteLength >> 1;
  if (view.byteOffset % 2 === 0) return new Int16Array(view.buffer, view.byteOffset, count);
  const out = new Int16Array(count);
  new Uint8Array(out.buffer).set(view.subarray(0, count * 2));
  return out;
}

/** The `tts.chunk` message the server would send for a fixture chunk. */
export function chunkMessage(utteranceId: string, chunk: TtsFixtureChunk, seq = 0): TtsChunkMsg {
  return {
    type: "tts.chunk",
    seq,
    utterance_id: utteranceId,
    chunk_id: chunk.chunk_id,
    index: chunk.index,
    text: chunk.text,
    sample_rate: chunk.sample_rate,
    duration_ms: chunk.duration_ms,
    visemes: chunk.visemes,
    word_timings: [],
  };
}

async function fetchOk(url: string, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(url, { signal, credentials: "same-origin" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response;
}

export async function loadTtsFixtureIndex(signal?: AbortSignal): Promise<TtsFixtureIndexEntry[]> {
  const response = await fetchOk(`${TTS_FIXTURE_BASE}index.json`, signal);
  const body = (await response.json()) as { fixtures?: TtsFixtureIndexEntry[] };
  return Array.isArray(body.fixtures) ? body.fixtures : [];
}

export interface LoadedTtsFixture {
  fixture: TtsFixture;
  /** PCM16 samples per chunk, in chunk order. */
  audio: Int16Array[];
}

export async function loadTtsFixture(id: string, signal?: AbortSignal): Promise<LoadedTtsFixture> {
  if (!/^[a-z0-9-]+$/.test(id)) throw new Error(`invalid fixture id: ${id}`);
  const fixture = (await (await fetchOk(`${TTS_FIXTURE_BASE}${id}.json`, signal)).json()) as TtsFixture;
  const audio = await Promise.all(
    fixture.chunks.map(async (chunk) => pcm16FromBytes(await (await fetchOk(`${TTS_FIXTURE_BASE}${chunk.audio}`, signal)).arrayBuffer())),
  );
  return { fixture, audio };
}
