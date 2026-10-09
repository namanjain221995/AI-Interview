/**
 * Local energy VAD on 20 ms microphone frames. It only drives instant UX
 * (playback ducking, the avatar's attentive listening, the mic meter and the
 * `vad.local` hint); the server's Silero VAD stays the authority for turns and
 * barge-in. An adaptive noise floor keeps it usable in noisy rooms.
 */
import { toDbfs } from "@/lib/realtime/pcm";

export interface EnergyVadOptions {
  frameMs?: number;
  /** Consecutive voiced frames before "speaking" (fast attack). */
  startFrames?: number;
  /** Silence needed before "not speaking" (hangover). */
  hangMs?: number;
  /** Absolute floor below which nothing counts as speech (dBFS). */
  minDb?: number;
  /** Speech must exceed the noise floor by this much (dB). */
  marginDb?: number;
  /** How fast the noise floor may rise (dB per second). */
  floorRiseDbPerSec?: number;
}

export interface VadResult {
  speaking: boolean;
  changed: boolean;
  /** 0-1 loudness for meters and the avatar. */
  level: number;
  db: number;
}

export class EnergyVad {
  private readonly frameMs: number;
  private readonly startFrames: number;
  private readonly hangFrames: number;
  private readonly minDb: number;
  private readonly marginDb: number;
  private readonly risePerFrame: number;
  private floorDb = -65;
  private voicedRun = 0;
  private silentRun = 0;
  private _speaking = false;

  constructor(options: EnergyVadOptions = {}) {
    this.frameMs = options.frameMs ?? 20;
    this.startFrames = options.startFrames ?? 3;
    this.hangFrames = Math.max(1, Math.round((options.hangMs ?? 450) / this.frameMs));
    this.minDb = options.minDb ?? -50;
    this.marginDb = options.marginDb ?? 12;
    this.risePerFrame = ((options.floorRiseDbPerSec ?? 4) * this.frameMs) / 1000;
  }

  get speaking(): boolean {
    return this._speaking;
  }

  get noiseFloorDb(): number {
    return this.floorDb;
  }

  /** Feed one frame's linear RMS (0-1). */
  push(rms: number): VadResult {
    const db = toDbfs(rms);
    const threshold = Math.max(this.minDb, this.floorDb + this.marginDb);
    const voiced = db >= threshold;

    // Track the floor: quick to fall, slow to rise. It keeps rising (at half speed)
    // while "voiced" so steady noise above the threshold is absorbed within seconds,
    // while real speech, which pauses constantly, keeps pulling it back down.
    if (db < this.floorDb) this.floorDb += (db - this.floorDb) * 0.25;
    else this.floorDb = Math.min(db, this.floorDb + (voiced ? this.risePerFrame / 2 : this.risePerFrame));

    let changed = false;
    if (voiced) {
      this.voicedRun += 1;
      this.silentRun = 0;
      if (!this._speaking && this.voicedRun >= this.startFrames) {
        this._speaking = true;
        changed = true;
      }
    } else {
      this.silentRun += 1;
      this.voicedRun = 0;
      if (this._speaking && this.silentRun >= this.hangFrames) {
        this._speaking = false;
        changed = true;
      }
    }
    const level = Math.max(0, Math.min(1, (db + 60) / 48));
    return { speaking: this._speaking, changed, level, db };
  }

  reset(): void {
    this.floorDb = -65;
    this.voicedRun = 0;
    this.silentRun = 0;
    this._speaking = false;
  }
}
