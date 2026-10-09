import { useEffect, useMemo, useState } from "react";

import { createAvatarController, resolveAvatarModel, type AvatarConfig, type AvatarController } from "@/features/avatar";
import type { PortalInterviewView } from "@/features/candidate/api";
import type { ClientPrefs } from "@/features/candidate/journey";

/**
 * The interviewer for this interview: the animated presence orb by default
 * ("orb:<palette>"), or the optional realistic 3D avatar (same-origin assets
 * only, CSP 'self'). Retired or unknown formats fall back to the orb.
 */
export function avatarConfig(interview: PortalInterviewView, prefs: ClientPrefs, reducedMotion: boolean): AvatarConfig {
  const avatar = interview.policy.avatar;
  const model = resolveAvatarModel(avatar?.model_format, avatar?.model_url);
  const background = avatar?.background === "office" || avatar?.background === "plain" ? avatar.background : "studio";
  return {
    ...model,
    interviewerName: interview.interviewerName,
    background,
    reducedMotion,
    renderMode: prefs.avatarMode === "3d" ? "auto" : prefs.avatarMode,
  };
}

/**
 * ONE avatar controller for the whole interview: it caches the loaded model, so it is
 * only replaced when the avatar settings themselves change. Keyed on the config's
 * values, so a refetched portal interview (a new object, e.g. after a window focus)
 * never disposes it, and layout switches only re-parent the stage that renders it.
 * Created in an effect so React StrictMode's double mount keeps one live instance.
 */
export function useAvatarController(interview: PortalInterviewView, prefs: ClientPrefs, reducedMotion: boolean): AvatarController | null {
  const next = avatarConfig(interview, prefs, reducedMotion);
  const key = JSON.stringify(next);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the key is the config's value
  const config = useMemo(() => next, [key]);
  const [controller, setController] = useState<AvatarController | null>(null);
  useEffect(() => {
    const created = createAvatarController(config);
    setController(created);
    return () => {
      created.dispose();
      setController(null);
    };
  }, [config]);
  return controller;
}
