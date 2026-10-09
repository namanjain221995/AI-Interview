# Wave C brief: premium 3D CARTOON interviewer (product owner request)

Read `wave-c-common.md` first. Load the "frontend-design" skill.

## Why
The product owner tried the interview and said the realistic 3D human (MPFB GLB) "looks too bad" and asked for a
**perfect 3D cartoon avatar with nice movement that looks good**. Realistic humans fall into the uncanny valley at this
fidelity; a well-designed stylised character does not. This is the most visible part of the product — treat it as the
centerpiece.

## Goal
A polished, appealing, professional **3D cartoon interviewer** (modern animated-film / premium app-avatar style:
friendly, warm, clean shapes, big expressive eyes, soft shading — NOT a blobby toy, NOT anime, NOT low-poly) that becomes
the **default** interviewer, with lively-but-professional motion and accurate lip-sync. Keep the realistic GLB selectable.

## Approach (licence-safe)
Build the character **procedurally in code** (three.js geometry + morph targets + skeleton, generated at runtime in
< ~100 ms, or baked by a build script into a GLB under `public/avatars/` from the same code). No third-party character
assets (no Ready Player Me / VRoid / Mixamo / marketplace models — licence risk). You may use three.js addons already in
`node_modules` (e.g. `three/examples/jsm/geometries/*`, `mergeVertices`, `toCreasedNormals`, `SubdivisionModifier`-style
smoothing you implement, `RoomEnvironment`).

Design the character like a character artist would:
- Proportions: slightly enlarged head (~1:4.5 to 1:5 body ratio in bust framing), large expressive eyes with iris, pupil,
  catch-light highlight and eyelids; soft nose; expressive mouth with teeth/tongue hint for visemes; rounded ears; sculpted
  stylised hair (a few smooth volumes, not a helmet); neck and shoulders that read well in bust framing.
- Outfit: smart-casual professional (blazer over shirt or a smart knit with collar), soft fabric shading, subtle details.
- Materials: stylised PBR (MeshPhysicalMaterial with sheen for skin warmth, soft rim light, gentle gradient), optional
  subtle toon ramp; clean, consistent palette that matches the app's calm enterprise design tokens; studio backdrop.
- At least **two variants** (e.g. `cartoon:maya` female, `cartoon:alex` male) sharing one rig.

## Rig & animation (must plug into the existing engine)
- Implement the existing `AvatarRig` interface (`src/features/avatar/rig/*`) with humanoid bones the pose library and
  animator already drive (Hips, Spine, Chest/Spine1/Spine2, Neck, Head, LeftShoulder/LeftArm/LeftForeArm/LeftHand + right,
  simplified fingers or mitten hands with a thumb that can do open-palm / explain gestures), eye bones or eye meshes for gaze.
- Morph targets: all 15 Oculus visemes (`sil PP FF TH DD kk CH SS nn RR aa E I O U`) shaped for a cartoon mouth (clear PP/MBP
  closures, FF lower-lip-to-teeth, round O/U), blinks (eyeBlinkLeft/Right, plus a closed-eye shape that looks good), brows
  (browInnerUp, browDownLeft/Right, browOuterUpLeft/Right), smile (mouthSmileLeft/Right), frown, jawOpen, cheek squint.
- Motion should feel alive and appealing (cartoon principles applied with restraint): breathing, subtle head bob/tilt while
  speaking, follow-through on gestures, anticipation before a nod, expressive brows on questions, warm smile on
  acknowledgements, attentive listening nods, natural blinks and eye darts. Reuse the existing behaviour controller states
  and pose/gesture library; tune per-rig retarget offsets so gestures look right on the new proportions (hands in frame,
  no clipping).
- Lip-sync: validate with the real-TTS fixtures and harness the avatar agent added (`src/features/avatar/dev/*`): bilabial
  closures visible, no jitter, mouth rests in pauses.

## Integration
- New config value: `modelFormat: "cartoon"` with `modelUrl: "cartoon:maya" | "cartoon:alex"` (backend `AvatarSettings` now
  defaults to `model_format="cartoon"`, `model_url="cartoon:maya"`; the pattern accepts `cartoon|glb|vrm|procedural`).
  Update `src/features/avatar/types.ts` (additive), the loader/controller, `src/lib/types.ts` `model_format` union,
  `src/features/interview/useAvatarController.ts`, `src/features/candidate/systemCheck/AvatarProbe.tsx` and the admin
  `src/features/recruiter/admin/settings/InterviewerSettings.tsx` (choose variant + preview) — you own these files for this
  change. Default everywhere = cartoon:maya.
- 2D fallback (`render/Fallback2D.tsx`) must match the cartoon character's look.
- Keep the public API (`index.ts`) backwards compatible; the realistic GLB path keeps working.

## Ownership
`apps/web/src/features/avatar/**`, `apps/web/public/avatars/**`, `apps/web/scripts/optimize-avatar.mjs`,
`apps/web/avatar-dev.html`, plus the integration files listed above.

## Quality bar & verification
- Iterate visually with Playwright on the GPU (`--use-angle=gl-egl --ignore-gpu-blocklist --enable-gpu`): front bust,
  3/4 view, every state, every gesture peak, speaking frame strips, both variants, several aspect ratios incl. mobile.
  Read every screenshot critically as an art director would; keep iterating until it genuinely looks premium and
  appealing (compare against your best judgement of top-tier stylised avatars). Include before/after images in the report.
- 60 fps on the GB10 GPU at DPR 1.75; generation time; bundle impact.
- Tests: `npx vitest run src/features/avatar` (add tests for the new rig: bone map, morph targets present, viseme mapping,
  variants), `npm run typecheck`, `npm run test`, `npm run build`.
