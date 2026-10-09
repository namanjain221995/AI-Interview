import { useEffect, useState } from "react";

/** Live 0-1 microphone level from a MediaStream (for meters). */
export function useAudioLevel(stream: MediaStream | null, fps = 24): { level: number; silent: boolean } {
  const [level, setLevel] = useState(0);
  const [silent, setSilent] = useState(false);

  useEffect(() => {
    const track = stream?.getAudioTracks()[0];
    if (!stream || !track) {
      setLevel(0);
      return;
    }
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(new MediaStream([track]));
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const data = new Float32Array(analyser.fftSize);
    let peak = 0;
    let frames = 0;
    let zeroFrames = 0;
    const interval = setInterval(() => {
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      let allZero = true;
      for (let i = 0; i < data.length; i++) {
        const v = data[i]!;
        if (v !== 0) allZero = false;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      const db = rms > 0 ? 20 * Math.log10(rms) : -100;
      const value = Math.max(0, Math.min(1, (db + 62) / 50));
      peak = Math.max(value, peak * 0.86);
      frames += 1;
      if (allZero) zeroFrames += 1;
      setLevel(peak);
      if (frames === fps * 2) setSilent(zeroFrames >= frames - 1);
    }, 1000 / fps);
    void ctx.resume().catch(() => undefined);
    return () => {
      clearInterval(interval);
      source.disconnect();
      void ctx.close().catch(() => undefined);
    };
  }, [stream, fps]);

  return { level, silent };
}
