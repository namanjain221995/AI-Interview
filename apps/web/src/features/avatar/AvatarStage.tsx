/**
 * <AvatarStage controller config className/>: renders the interviewer in 3D
 * (WebGL), as a 2D portrait (no WebGL / context lost / render failure) or not
 * at all ("none": audio + text only). With modelFormat "orb" the interviewer
 * is the animated presence orb (3D, or its Canvas2D twin) instead of a
 * character. Mode changes and failures are reported through
 * controller.getStatus()/subscribe(); nothing is ever thrown to the parent tree.
 *
 * The stage can be re-parented or resized freely: the controller keeps the
 * loaded model, so a remount only re-uploads it to the new canvas. After a
 * WebGL context loss the stage shows the 2D portrait and tries 3D again once
 * the GPU is back (see render/contextRestore.ts).
 */
import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import { getAvatarInternals } from "./controller";
import { ContextRestorePolicy } from "./render/contextRestore";
import Orb2D from "./orb/Orb2D";
import Fallback2D from "./render/Fallback2D";
import { isWebGLAvailable } from "./render/environment";
import type { AvatarFraming } from "./render/Avatar3D";
import type { AvatarConfig, AvatarController, AvatarRenderMode } from "./types";

const Avatar3D = lazy(() => import("./render/Avatar3D"));
const Orb3D = lazy(() => import("./orb/Orb3D"));

class StageErrorBoundary extends Component<{ onError: (error: unknown) => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error("[avatar] render failure", error, info.componentStack);
    this.props.onError(error);
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

export interface AvatarStageProps {
  controller: AvatarController;
  /** Defaults to the config the controller was created with. */
  config?: AvatarConfig;
  className?: string;
  /** Camera framing for the 3D view (default "bust"). */
  framing?: AvatarFraming;
  /** Adaptive pixel ratio under load (default true). */
  adaptiveQuality?: boolean;
  /** The session is paused or offline: the presence orb desaturates and breathes slowly. */
  dimmed?: boolean;
  /**
   * A tile covers this corner of the stage (e.g. the candidate's camera in the
   * coding corner): the presence orb sits off-centre, clear of it.
   */
  occludedCorner?: "bottom-right";
}

export function AvatarStage({ controller, config: configProp, className, framing, adaptiveQuality, dimmed = false, occludedCorner }: AvatarStageProps) {
  const internals = getAvatarInternals(controller);
  const config = configProp ?? internals?.config ?? null;
  const requested = config?.renderMode ?? "auto";
  const webgl = useMemo(() => (requested === "auto" || requested === "3d" ? isWebGLAvailable() : false), [requested]);
  const [failure, setFailure] = useState<string | null>(null);
  const restore = useRef<ContextRestorePolicy | null>(null);
  restore.current ??= new ContextRestorePolicy();

  // A new model or an explicit mode request is a fresh attempt at 3D.
  useEffect(() => {
    setFailure(null);
  }, [requested, config?.modelUrl, config?.modelFormat]);

  // Context lost: 2D now, then probe for a fresh WebGL context and remount 3D.
  useEffect(() => {
    if (failure !== "webgl_context_lost") return;
    const policy = restore.current!;
    let delay = policy.onLost(performance.now());
    let timer: ReturnType<typeof setTimeout> | undefined;
    const probe = () => {
      if (isWebGLAvailable(true)) {
        policy.onRestored(performance.now());
        setFailure(null);
        return;
      }
      delay = policy.onProbeFailed();
      if (delay !== null) timer = setTimeout(probe, delay);
    };
    if (delay !== null) timer = setTimeout(probe, delay);
    return () => clearTimeout(timer);
  }, [failure]);

  const mode: AvatarRenderMode = requested === "none" ? "none" : requested === "2d" ? "2d" : webgl && !failure ? "3d" : "2d";

  useEffect(() => {
    if (internals && config?.reducedMotion !== undefined) internals.engine.setReducedMotion(config.reducedMotion);
  }, [internals, config?.reducedMotion]);

  useEffect(() => {
    if (!internals) return;
    if (mode === "none") internals.setStatus({ mode, loaded: true, fps: 0, dpr: undefined });
    else if (mode === "2d") {
      const reason = failure ?? (requested !== "2d" && !webgl ? "webgl_unavailable" : undefined);
      internals.setStatus(reason ? { mode, loaded: true, error: reason, dpr: undefined } : { mode, loaded: true, dpr: undefined });
    } else internals.setRenderMode("3d");
  }, [internals, mode, failure, requested, webgl]);

  if (!internals || !config) {
    // A foreign AvatarController implementation: nothing we can drive.
    return <div className={className} data-avatar-mode="none" />;
  }

  const background = config.background ?? "studio";
  const orb = config.modelFormat === "orb";
  const label = `${config.interviewerName || "Interviewer"}, AI interviewer`;
  return (
    <div
      className={`relative overflow-hidden ${className ?? ""}`}
      data-avatar-mode={mode}
      {...(mode === "none" ? {} : { role: "img", "aria-label": label })}
    >
      <StageErrorBoundary
        key={`${mode}:${config.modelUrl}:${config.modelFormat}`}
        onError={(error) => {
          internals.reportRenderError(error);
          setFailure(`render_error: ${error instanceof Error ? error.message : String(error)}`);
        }}
      >
        {mode === "3d" && orb && (
          <Suspense fallback={<Orb2D internals={internals} modelUrl={config.modelUrl} dimmed={dimmed} occludedCorner={occludedCorner} />}>
            <Orb3D
              internals={internals}
              config={config}
              dimmed={dimmed}
              occludedCorner={occludedCorner}
              adaptiveQuality={adaptiveQuality}
              onContextLost={() => setFailure((prev) => prev ?? "webgl_context_lost")}
            />
          </Suspense>
        )}
        {mode === "3d" && !orb && (
          <Suspense fallback={<Fallback2D internals={internals} background={background} name={config.interviewerName} />}>
            <Avatar3D
              internals={internals}
              config={config}
              framing={framing}
              adaptiveQuality={adaptiveQuality}
              onContextLost={() => setFailure((prev) => prev ?? "webgl_context_lost")}
            />
          </Suspense>
        )}
        {mode === "2d" && orb && <Orb2D internals={internals} modelUrl={config.modelUrl} dimmed={dimmed} occludedCorner={occludedCorner} />}
        {mode === "2d" && !orb && <Fallback2D internals={internals} background={background} name={config.interviewerName} />}
      </StageErrorBoundary>
    </div>
  );
}

export default AvatarStage;
