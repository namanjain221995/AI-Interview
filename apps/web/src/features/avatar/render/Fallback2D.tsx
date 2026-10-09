/**
 * 2D fallback portrait (SVG) used when WebGL is unavailable or the context is
 * lost. Driven by the same engine frames as the 3D avatar: blinks, gaze,
 * brows, smile, mouth shapes from visemes, breathing, head tilts and nods.
 * Updates DOM attributes directly in a rAF loop (no React re-render per frame).
 */
import { useEffect, useId, useRef } from "react";
import { breathCurve } from "../animation/poses";
import type { AvatarInternals } from "../controller";
import { clamp } from "../core/math";
import type { AvatarFrame } from "../engine";
import { BACKDROP_COLORS, type BackdropKind } from "./environment";

const SKIN = "#cf9d80";
const SKIN_SHADE = "#b98468";
const HAIR = "#2b1e17";
const LIP = "#b46c66";

const POSE_TILT: Record<string, number> = {
  listening_attentive: -2.5,
  listening_tilt: 3.5,
  thinking: 4,
  speaking_engaged: -1.5,
  interrupted_lean: -1.2,
  closing: -2.2,
  idle_shift: 1.2,
};

function headMotion(frame: AvatarFrame): { tilt: number; nod: number } {
  let tilt = POSE_TILT[frame.behavior.base.pose] ?? 0;
  let nod = 0;
  const g = frame.behavior.headGesture;
  if (g) {
    const p = (frame.now - g.startedAt) / g.duration;
    if (p >= 0 && p < 1) {
      if (g.id === "head_tilt") tilt += 6 * g.intensity * Math.sin(Math.PI * Math.min(1, p * 1.6));
      else nod = 5 * g.intensity * Math.sin(Math.PI * p * (g.id === "nod" || g.id === "positive_nod" ? 2 : 1)) ** 2;
    }
  }
  return { tilt, nod };
}

export interface Fallback2DProps {
  internals: AvatarInternals;
  background: BackdropKind;
  name: string;
  /** Render rate cap (default 30 fps: plenty for a 2D portrait, half the raster cost). */
  maxFps?: number;
}

export default function Fallback2D({ internals, background, name, maxFps = 30 }: Fallback2DProps) {
  const uid = useId().replace(/:/g, "");
  const refs = {
    body: useRef<SVGGElement>(null),
    head: useRef<SVGGElement>(null),
    irisL: useRef<SVGGElement>(null),
    irisR: useRef<SVGGElement>(null),
    lidL: useRef<SVGRectElement>(null),
    lidR: useRef<SVGRectElement>(null),
    lashL: useRef<SVGPathElement>(null),
    lashR: useRef<SVGPathElement>(null),
    browL: useRef<SVGPathElement>(null),
    browR: useRef<SVGPathElement>(null),
    mouth: useRef<SVGPathElement>(null),
    mouthClip: useRef<SVGPathElement>(null),
    lips: useRef<SVGPathElement>(null),
    teeth: useRef<SVGRectElement>(null),
    blushL: useRef<SVGEllipseElement>(null),
    blushR: useRef<SVGEllipseElement>(null),
  };
  const refsRef = useRef(refs);
  refsRef.current = refs;

  useEffect(() => {
    let raf = 0;
    let frames = 0;
    let since = performance.now();
    let lastRender = -Infinity;
    const minInterval = 1000 / Math.max(1, maxFps) - 2;
    const engine = internals.engine;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const nowMs = performance.now();
      if (nowMs - lastRender < minInterval) return;
      lastRender = nowMs;
      let frame: AvatarFrame;
      try {
        frame = engine.update();
      } catch (err) {
        console.error("[avatar] 2D frame failed", err);
        return;
      }
      const r = refsRef.current;
      const breath = breathCurve(frame.now);
      r.body.current?.setAttribute("transform", `translate(0 ${(-1.6 * breath * (0.5 + 0.5 * frame.motionScale)).toFixed(2)})`);
      const { tilt, nod } = headMotion(frame);
      const sway = Math.sin(frame.now * 0.7) * 0.6 * frame.motionScale;
      const headYaw = (frame.gaze.headYaw * 180) / Math.PI;
      r.head.current?.setAttribute(
        "transform",
        `translate(${(headYaw * 0.8).toFixed(2)} ${(nod - 1.2 * breath).toFixed(2)}) rotate(${(tilt + sway).toFixed(2)} 200 300)`,
      );

      const dx = clamp(frame.gaze.eyeYaw * 30, -4, 4);
      const dy = clamp(-frame.gaze.eyePitch * 30, -3, 3);
      r.irisL.current?.setAttribute("transform", `translate(${dx.toFixed(2)} ${dy.toFixed(2)})`);
      r.irisR.current?.setAttribute("transform", `translate(${dx.toFixed(2)} ${dy.toFixed(2)})`);
      const blinkL = frame.face.eyeBlinkLeft ?? 0;
      const blinkR = frame.face.eyeBlinkRight ?? 0;
      // Viewer's left is the character's right.
      const lid = (el: SVGRectElement | null, lash: SVGPathElement | null, cx: number, b: number) => {
        const bottom = 206.5 + 15.5 * b;
        el?.setAttribute("height", (bottom - 196).toFixed(2));
        lash?.setAttribute("d", `M${cx - 13} ${(bottom + 4.5).toFixed(2)} Q${cx} ${(bottom - 3.5).toFixed(2)} ${cx + 13} ${(bottom + 4.5).toFixed(2)}`);
      };
      lid(r.lidR.current, r.lashR.current, 174, blinkR);
      lid(r.lidL.current, r.lashL.current, 226, blinkL);

      const brow = -6 * frame.browRaise;
      const inner = -3 * (frame.face.browInnerUp ?? 0);
      r.browR.current?.setAttribute("d", `M160 ${194 + brow} Q174 ${188 + brow} 188 ${193 + brow + inner}`);
      r.browL.current?.setAttribute("d", `M212 ${193 + brow + inner} Q226 ${188 + brow} 240 ${194 + brow}`);

      const open = clamp(frame.mouthOpen, 0, 1);
      const w = 21 * (1 + 0.22 * frame.mouthWide - 0.3 * frame.mouthRound);
      // Quadratic curves reach half their control offset: controls sit at 2x the opening.
      const h = 1 + 18 * open;
      const lift = 5 * frame.smile;
      const up = 2 * (h * 0.3) + 0.8;
      const down = 2 * (h * 0.7) + 0.8;
      const y0 = 268;
      const d = `M${(200 - w).toFixed(2)} ${(y0 - lift).toFixed(2)} Q200 ${(y0 - up - lift * 0.3).toFixed(2)} ${(200 + w).toFixed(2)} ${(y0 - lift).toFixed(2)} Q200 ${(y0 + down).toFixed(2)} ${(200 - w).toFixed(2)} ${(y0 - lift).toFixed(2)} Z`;
      r.mouth.current?.setAttribute("d", d);
      r.mouthClip.current?.setAttribute("d", d);
      r.lips.current?.setAttribute("d", d);
      r.lips.current?.setAttribute("stroke-width", (2.6 + 1.2 * frame.lipsPressed).toFixed(2));
      r.teeth.current?.setAttribute("y", (y0 - up / 2 - 1).toFixed(2));
      const blush = (0.12 + 0.25 * frame.smile).toFixed(3);
      r.blushL.current?.setAttribute("opacity", blush);
      r.blushR.current?.setAttribute("opacity", blush);

      frames += 1;
      const now = performance.now();
      if (now - since >= 1000) {
        internals.reportFps((frames * 1000) / (now - since));
        frames = 0;
        since = now;
      }
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [internals, maxFps]);

  const bg = BACKDROP_COLORS[background];
  const id = (s: string) => `${s}-${uid}`;
  return (
    <div
      className="absolute inset-0"
      // Static backdrop as CSS so per-frame SVG updates only repaint the figure.
      style={{ background: `radial-gradient(ellipse 70% 85% at 50% 38%, ${bg.glow} 0%, ${bg.base} 55%, ${bg.edge} 100%)` }}
    >
    <svg
      viewBox="0 0 400 480"
      preserveAspectRatio="xMidYMax meet"
      className="absolute inset-0 h-full w-full"
      aria-hidden="true"
      data-avatar-portrait={name}
    >
      <defs>
        <linearGradient id={id("suit")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#3b455a" />
          <stop offset="100%" stopColor="#252c3b" />
        </linearGradient>
        <radialGradient id={id("skin")} cx="45%" cy="35%" r="75%">
          <stop offset="0%" stopColor="#e0b294" />
          <stop offset="100%" stopColor={SKIN_SHADE} />
        </radialGradient>
        <clipPath id={id("eyeR")}>
          <ellipse cx="174" cy="214" rx="13" ry="7.5" />
        </clipPath>
        <clipPath id={id("eyeL")}>
          <ellipse cx="226" cy="214" rx="13" ry="7.5" />
        </clipPath>
        <clipPath id={id("mouth")}>
          <path ref={refs.mouthClip} d="M179 268 Q200 267 221 268 Q200 269 179 268 Z" />
        </clipPath>
      </defs>
      <g ref={refs.body}>
        <g transform="translate(0 -12)">
          <path
            d="M36 492 C 50 402, 104 370, 200 362 C 296 370, 350 402, 364 492 Z"
            fill={`url(#${id("suit")})`}
            stroke="#56627a"
            strokeOpacity="0.4"
            strokeWidth="1.5"
          />
          <path d="M168 360 L232 360 L200 446 Z" fill="#e3e7ee" />
          <path d="M168 360 L200 446 L188 452 L150 394 L161 385 L148 372 Z" fill="#2c3446" />
          <path d="M232 360 L200 446 L212 452 L250 394 L239 385 L252 372 Z" fill="#2c3446" />
          <path d="M170 350 Q200 372 230 350 L238 364 Q200 390 162 364 Z" fill="#d9dee6" />
        </g>
      </g>
      <g ref={refs.head}>
        <path d="M177 292 L177 342 Q200 356 223 342 L223 292 Z" fill={SKIN_SHADE} />
        <path d="M128 210 C 124 140, 160 110, 200 110 C 240 110, 276 140, 272 210 C 272 246, 262 262, 254 270 L146 270 C 138 262, 128 246, 128 210 Z" fill={HAIR} />
        <ellipse cx="135" cy="228" rx="9" ry="17" fill={SKIN_SHADE} />
        <ellipse cx="265" cy="228" rx="9" ry="17" fill={SKIN_SHADE} />
        <path d="M137 208 C 137 152, 166 128, 200 128 C 234 128, 263 152, 263 208 C 263 262, 236 300, 200 304 C 164 300, 137 262, 137 208 Z" fill={`url(#${id("skin")})`} />
        <path d="M134 202 C 130 146, 166 116, 204 116 C 244 116, 272 146, 266 206 C 258 176, 238 154, 214 150 C 196 166, 168 170, 146 168 C 140 178, 136 190, 134 202 Z" fill={HAIR} />
        <ellipse ref={refs.blushR} cx="165" cy="246" rx="13" ry="7" fill="#e98f84" opacity="0.15" />
        <ellipse ref={refs.blushL} cx="235" cy="246" rx="13" ry="7" fill="#e98f84" opacity="0.15" />
        <path ref={refs.browR} d="M160 194 Q174 188 188 193" stroke={HAIR} strokeWidth="4.2" fill="none" strokeLinecap="round" />
        <path ref={refs.browL} d="M212 193 Q226 188 240 194" stroke={HAIR} strokeWidth="4.2" fill="none" strokeLinecap="round" />
        {(
          [
            ["R", 174, refs.irisR, refs.lidR, refs.lashR],
            ["L", 226, refs.irisL, refs.lidL, refs.lashL],
          ] as const
        ).map(([side, cx, irisRef, lidRef, lashRef]) => (
          <g key={side}>
            <ellipse cx={cx} cy="214" rx="13" ry="7.5" fill="#f4f0ea" />
            <g clipPath={`url(#${id(`eye${side}`)})`}>
              <g ref={irisRef}>
                <circle cx={cx} cy="214" r="6.3" fill="#5a3b26" />
                <circle cx={cx} cy="214" r="2.9" fill="#0b0807" />
                <circle cx={cx + 2} cy="211.8" r="1.3" fill="#ffffff" opacity="0.85" />
              </g>
              <rect ref={lidRef} x={cx - 16} y="196" width="32" height="10.5" fill={SKIN} />
            </g>
            <path ref={lashRef} d={`M${cx - 13} 211 Q${cx} 203 ${cx + 13} 211`} stroke="#231813" strokeWidth="2" fill="none" strokeLinecap="round" />
          </g>
        ))}
        <path d="M200 222 Q 196 244, 192 251 Q 200 257, 208 251" stroke="#a87660" strokeWidth="2" fill="none" strokeLinecap="round" />
        <path ref={refs.mouth} d="M179 268 Q200 267 221 268 Q200 269 179 268 Z" fill="#4a1c1e" />
        <rect ref={refs.teeth} x="181" y="266" width="38" height="5" fill="#f2eee6" clipPath={`url(#${id("mouth")})`} />
        <path ref={refs.lips} d="M179 268 Q200 267 221 268 Q200 269 179 268 Z" fill="none" stroke={LIP} strokeWidth="2.6" strokeLinejoin="round" />
      </g>
    </svg>
    </div>
  );
}
