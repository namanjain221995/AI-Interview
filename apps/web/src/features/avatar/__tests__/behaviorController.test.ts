import { describe, expect, it } from "vitest";
import {
  BehaviorController,
  INTERRUPT_RECOVERY,
  MIN_DWELL,
  transitionDuration,
} from "../behavior/behaviorController";
import { ARM_GESTURE_DURATION } from "../animation/catalog";

const make = (seed = 42, reducedMotion = false) => new BehaviorController({ seed, now: -10, reducedMotion });

describe("BehaviorController transitions and dwell times", () => {
  it("enters a state immediately once the current dwell has elapsed", () => {
    const c = make();
    c.setState("LISTENING", 0);
    const s = c.update(0);
    expect(s.state).toBe("LISTENING");
    expect(s.base.pose).toMatch(/^listening_/);
    expect(s.base.fade).toBeCloseTo(transitionDuration("IDLE", "LISTENING"));
  });

  it("defers requests inside the minimum dwell and applies them when it expires", () => {
    const c = make();
    c.setState("LISTENING", 0);
    c.setState("SPEAKING", 0.2);
    let s = c.update(0.3);
    expect(s.state).toBe("LISTENING");
    expect(s.pending).toBe("SPEAKING");
    s = c.update(MIN_DWELL.LISTENING - 0.01);
    expect(s.state).toBe("LISTENING");
    s = c.update(MIN_DWELL.LISTENING + 0.01);
    expect(s.state).toBe("SPEAKING");
    expect(s.pending).toBeNull();
    expect(s.stateSince).toBeCloseTo(MIN_DWELL.LISTENING + 0.01);
  });

  it("latest deferred request wins", () => {
    const c = make();
    c.setState("LISTENING", 0);
    c.setState("THINKING", 0.1);
    c.applyBehavior({ state: "QUESTIONING", expression: "curious" }, 0.2);
    const s = c.update(0.5);
    expect(s.state).toBe("QUESTIONING");
    expect(s.expression).toBe("curious");
  });

  it("a request for the current state cancels a deferred one", () => {
    const c = make();
    c.setState("LISTENING", 0);
    c.setState("SPEAKING", 0.1);
    c.applyBehavior({ state: "LISTENING", expression: "warm" }, 0.2);
    const s = c.update(1);
    expect(s.state).toBe("LISTENING");
    expect(s.expression).toBe("warm");
  });

  it("INTERRUPTED is immediate, cancels gestures, leans in and recovers to LISTENING", () => {
    const c = make();
    c.applyBehavior({ state: "SPEAKING", gesture: "two_hand_explain", gesture_intensity: "high" }, 0);
    let s = c.update(0.1);
    expect(s.gesture).not.toBeNull();
    const cancel = s.cancelSerial;
    c.interrupt(0.15); // well inside SPEAKING's dwell
    s = c.update(0.15);
    expect(s.state).toBe("INTERRUPTED");
    expect(s.base.pose).toBe("interrupted_lean");
    expect(s.base.fade).toBeCloseTo(0.18);
    expect(s.cancelSerial).toBe(cancel + 1);
    expect(s.gesture).toBeNull();
    expect(s.lean).toBe(1);
    s = c.update(0.15 + INTERRUPT_RECOVERY - 0.05);
    expect(s.state).toBe("INTERRUPTED");
    s = c.update(0.15 + INTERRUPT_RECOVERY + 0.01);
    expect(s.state).toBe("LISTENING");
    expect(s.base.fade).toBeCloseTo(0.6);
    // Lean eases out over 0.8 s.
    expect(c.update(0.15 + INTERRUPT_RECOVERY + 0.4).lean).toBeGreaterThan(0);
    expect(c.update(0.15 + INTERRUPT_RECOVERY + 0.85).lean).toBe(0);
  });

  it("maps semantic gestures to clips without immediate repeats (seeded)", () => {
    const c = make(7);
    const ids: string[] = [];
    let t = 0;
    c.setState("SPEAKING", t);
    for (let i = 0; i < 12; i++) {
      c.applyBehavior({ state: "SPEAKING", gesture: "one_hand_explain", gesture_intensity: "medium" }, t);
      const g = c.update(t).gesture!;
      expect(g).not.toBeNull();
      ids.push(g.id);
      t += ARM_GESTURE_DURATION.one_hand_explain_r + 0.5;
    }
    for (let i = 1; i < ids.length; i++) expect(ids[i]).not.toBe(ids[i - 1]);
    // Determinism: the same seed yields the same sequence.
    const d = make(7);
    let u = 0;
    d.setState("SPEAKING", u);
    const again: string[] = [];
    for (let i = 0; i < 12; i++) {
      d.applyBehavior({ state: "SPEAKING", gesture: "one_hand_explain", gesture_intensity: "medium" }, u);
      again.push(d.update(u).gesture!.id);
      u += ARM_GESTURE_DURATION.one_hand_explain_r + 0.5;
    }
    expect(again).toEqual(ids);
  });

  it("drops duplicate gesture requests and defers overlapping ones", () => {
    const c = make();
    c.setState("SPEAKING", 0);
    c.applyBehavior({ state: "SPEAKING", gesture: "small_open_palm" }, 0.1);
    const first = c.update(0.1).gesture!;
    c.applyBehavior({ state: "SPEAKING", gesture: "small_open_palm" }, 0.5);
    expect(c.update(0.5).gesture!.serial).toBe(first.serial); // duplicate dropped
    c.applyBehavior({ state: "SPEAKING", gesture: "two_hand_explain" }, 0.6);
    expect(c.update(0.6).gesture!.serial).toBe(first.serial); // deferred: first still running
    const later = c.update(0.1 + first.duration * 0.75).gesture!;
    expect(later.id).toMatch(/^two_hand_explain_/);
  });

  it("scales gesture intensity by level and tone, and damps it for reduced motion", () => {
    const weight = (intensity: "low" | "medium" | "high", tone: "professional" | "friendly", reduced = false) => {
      const c = make(1, reduced);
      c.applyBehavior({ state: "SPEAKING", gesture: "small_emphasis", gesture_intensity: intensity, tone }, 0);
      return c.update(0).gesture!.intensity;
    };
    expect(weight("low", "friendly")).toBeLessThan(weight("medium", "friendly"));
    expect(weight("medium", "friendly")).toBeLessThan(weight("high", "friendly"));
    expect(weight("high", "professional")).toBeLessThan(weight("high", "friendly"));
    expect(weight("high", "friendly", true)).toBeLessThan(weight("high", "friendly"));
    expect(weight("high", "friendly")).toBeLessThanOrEqual(1);
  });

  it("routes nod / head tilt to the head layer and implies motion per state", () => {
    const c = make();
    c.applyBehavior({ state: "LISTENING", gesture: "nod" }, 0);
    expect(c.update(0).headGesture!.id).toBe("nod");
    c.applyBehavior({ state: "LISTENING", gesture: "head_tilt" }, 2);
    expect(c.update(2).headGesture!.id).toBe("head_tilt");
    c.setState("ACKNOWLEDGING", 5);
    const s = c.update(5);
    expect(s.state).toBe("ACKNOWLEDGING");
    expect(s.headGesture!.id).toBe("nod_small");
    expect(s.expression).toBe("warm");
    c.setState("THINKING", 6);
    expect(c.update(6).look).toBe("thinking");
  });

  it("enters SPEAKING when audio starts and returns to LISTENING after it ends", () => {
    const c = make();
    c.setState("LISTENING", 0);
    c.setSpeechActive(true, 1);
    expect(c.update(1).state).toBe("SPEAKING");
    c.setSpeechActive(false, 3);
    expect(c.update(4).state).toBe("SPEAKING");
    expect(c.update(4.25).state).toBe("LISTENING");
  });

  it("adds small beat gestures while speaking, but not with reduced motion", () => {
    const count = (reduced: boolean) => {
      const c = make(3, reduced);
      c.applyBehavior({ state: "SPEAKING", tone: "friendly" }, 0);
      c.setSpeechActive(true, 0);
      const serials = new Set<number>();
      for (let t = 0; t < 60; t += 0.05) {
        const g = c.update(t).gesture;
        if (g) serials.add(g.serial);
      }
      return serials.size;
    };
    expect(count(false)).toBeGreaterThan(1);
    expect(count(true)).toBe(0);
  });

  it("backchannel nods at candidate pauses are occasional and spaced", () => {
    const c = make(11);
    c.setState("LISTENING", 0);
    const nods: number[] = [];
    let last = -1;
    for (let t = 0; t < 120; t += 0.05) {
      const phrase = t % 3;
      c.setCandidateSpeaking(true, phrase < 2.2 ? 0.3 : 0.01, t);
      const h = c.update(t).headGesture;
      if (h && h.serial !== last) {
        last = h.serial;
        if (h.id === "backchannel_nod") nods.push(h.startedAt);
      }
    }
    expect(nods.length).toBeGreaterThan(3);
    expect(nods.length).toBeLessThan(40); // occasional, not every pause
    for (let i = 1; i < nods.length; i++) expect(nods[i]! - nods[i - 1]!).toBeGreaterThanOrEqual(4.5);
  });

  it("rotates idle variants occasionally (weight shift) without repeating", () => {
    const c = make(5);
    const poses: string[] = [];
    let serial = -1;
    for (let t = 0; t < 200; t += 0.25) {
      const s = c.update(t);
      if (s.base.serial !== serial) {
        serial = s.base.serial;
        poses.push(s.base.pose);
      }
    }
    expect(poses.length).toBeGreaterThan(4);
    for (let i = 1; i < poses.length; i++) expect(poses[i]).not.toBe(poses[i - 1]);
  });

  it("ignores unknown tone / intensity / expression values instead of producing NaN", () => {
    const c = make();
    c.applyBehavior(
      { state: "SPEAKING", tone: "sarcastic" as never, gesture_intensity: "extreme" as never, expression: "smug" as never, gesture: "small_open_palm" },
      0,
    );
    const s = c.update(0);
    expect(s.tone).toBe("professional");
    expect(Number.isFinite(s.expressionIntensity)).toBe(true);
    expect(s.expression).toBe("neutral");
    expect(Number.isFinite(s.gesture!.intensity)).toBe(true);
    // Prototype keys are not enum values either.
    c.applyBehavior({ state: "SPEAKING", tone: "toString" as never, expression: "constructor" as never }, 5);
    const t = c.update(5);
    expect(t.tone).toBe("professional");
    expect(Number.isFinite(t.expressionIntensity)).toBe(true);
  });

  it("uses per-transition crossfade durations", () => {
    expect(transitionDuration("SPEAKING", "INTERRUPTED")).toBeCloseTo(0.18);
    expect(transitionDuration("INTERRUPTED", "LISTENING")).toBeCloseTo(0.6);
    expect(transitionDuration("LISTENING", "THINKING")).toBeCloseTo(0.7);
    expect(transitionDuration("SPEAKING", "CLOSING")).toBeCloseTo(0.8);
    expect(transitionDuration("IDLE", "IDLE")).toBeGreaterThan(1);
  });
});
