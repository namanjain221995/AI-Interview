/**
 * On-device face check. Import this module lazily (`import("@/features/integrity/face")`)
 * so MediaPipe only loads in the system check and the interview room.
 */
export { analyseFrame, classifyGuidance, GUIDANCE_THRESHOLDS, MIN_FACE_SCORE, regionLuma, regionSharpness, roughYaw, type FaceLight, type FrameAnalysis, type GuidanceKind } from "./analysis";
export { createBlazeFaceDetector, DETECTOR_NAME, FaceCheckUnsupportedError, MEDIAPIPE_ASSETS, mediapipeBase, type FaceDetectorEngine } from "./detector";
export { clipBox, coverLayout, iou, overlayRect, type FaceBox } from "./geometry";
export { FACE_GUIDANCE_COPY, FACE_OK_COPY, GuidanceThrottle, ROOM_TIP_KINDS } from "./guidance";
export { FrameCalm, lumaPatch, MotionMeter } from "./liveness";
export { FacePresenceMonitor, type FaceMonitorPerf, type FaceMonitorStatus, type FaceMonitorView, type FaceMonitorViewFace, type FacePresenceMonitorOptions, type FaceSnapshotRequest } from "./monitor";
export { DEFAULT_FACE_THRESHOLDS, FacePresenceStates, type FaceGuidance, type FaceIntervalKind, type FaceSummaryCounters, type FaceThresholds, type FaceTransition } from "./states";
export { FaceTracker, type RawDetection, type TrackedFace } from "./tracker";
