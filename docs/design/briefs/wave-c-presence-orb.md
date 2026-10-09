# Wave C brief: animated interviewer presence ("voice orb") replaces the avatar

Read `wave-c-common.md` first. Load the "frontend-design" skill.

## Why
The product owner rejected both character avatars: the realistic 3D human "looks too bad" and the procedural cartoon
"looks too odd". Owner decision (2026-10-07): **no avatar; use something animated instead.** We ship an abstract,
audio-reactive presence (the pattern used by modern voice AI products: a glowing orb that breathes, listens, thinks and
pulses with the voice). It cannot fall into the uncanny valley, it reads as premium when done well, and it is cheap on
the GPU. The owner asked specifically how it behaves "when it speaks": speaking must be the most convincing state.

## Goal
A polished animated interviewer presence that becomes the **default** interviewer visual everywhere (interview room,
system check, admin preview), rendered behind the existing `AvatarController` contract so the interview room, realtime
runtime and E2E selectors need no behavioural changes.

## Visual design
- A sphere about 40-45% of the stage height (bust-framing slot), centred, on a calm backdrop that matches the app's
  design tokens (soft radial vignette; light and dark themes both look right).
- Surface: high-detail sphere with smooth 3D-noise vertex displacement (low amplitude, organic, never spiky), a
  fragment shader with domain-warped internal colour flow over a 3-4 stop palette, fresnel rim glow, a soft specular
  highlight and a subtle glassy depth. Add a soft additive halo/bloom (sprite or shader; no new npm packages).
- Palettes via `modelUrl` `orb:<palette>`: `aurora` (default: teal, indigo, violet, a touch of pink), `sunrise`
  (amber, coral, rose), `ocean` (cyan, blue, deep navy). Unknown palette falls back to `aurora`.
- Everything eased; no hard cuts. Motion must feel alive but calm and professional.

## Behaviour (map the existing states; keep it restrained)
| Situation | Orb behaviour |
|---|---|
| IDLE / waiting | slow breathing (about ±2% scale, ~4 s period), slow colour drift |
| LISTENING / candidate speaking | attentive: slightly tighter, cooler hue; gentle ripples driven by the candidate's live mic level (`setCandidateSpeaking(true, level)` / runtime mic level) so the candidate sees it hears them; settles when they pause |
| ACKNOWLEDGING | one soft "nod" bounce and a warm brighten |
| THINKING (turn `processing`) | internal flow speeds up into a gentle swirl, hue shifts towards violet, slight shimmer; no fake typing dots |
| SPEAKING / QUESTIONING / ENCOURAGING | **driven by the voice the candidate actually hears**: amplitude from the playback `AnalyserNode` (after the duck gain, so barge-in ducking shows) plus the viseme cues on the AudioContext clock for articulation (open vowels push bigger pulses, `PP`/`MBP` closures dip). Attack ~25 ms, release ~140 ms, so syllables are visible and pauses settle within ~200 ms. Warmer hue while speaking; stressed syllables emit a faint outward ripple ring. Zero perceptible lag vs audio |
| QUESTIONING | small upward lift/brighten at the end of the question (curious) |
| INTERRUPTED (barge-in) | quick dim and contract as the audio ducks, then LISTENING |
| TRANSITION | smooth colour sweep |
| CLOSING / goodbye | warm glow, slow fade to a gentle rest |
| Paused / disconnected | desaturated, very slow breathing |

Expressions and gestures from the server map to subtle variations only: `warm`/`pleased`/`encouraging` warmer and
softer, `curious`/`question_gesture`/`head_tilt` brighter with a slight lift, `serious`/`thoughtful` cooler and slower,
`nod` a soft bounce, `small_emphasis`/`*_explain` a slightly bigger pulse on the next stressed syllable, `goodbye` the
closing glow.

## Fallbacks and accessibility
- 2D fallback (no WebGL, low power, `renderMode: "2d"`): a Canvas2D/CSS orb with the same palette and the same signals
  (scale + glow + ripple), visually consistent with the 3D one.
- `prefers-reduced-motion`: no displacement or ripples; gentle opacity/brightness changes only.
- Screen readers: the room already announces captions/state; do not add competing live regions.
- Compact mode (coding workspace corner, 120-200 px): must still look good (fewer segments, smaller halo).
- Pause rendering when the tab is hidden; adaptive DPR (cap 1.75); 60 fps on the GB10 GPU with < ~2 ms GPU per frame.

## Integration
- New `modelFormat: "orb"` with `modelUrl: "orb:aurora" | "orb:sunrise" | "orb:ocean"`. Backend `AvatarSettings` already
  defaults to `model_format="orb"`, `model_url="orb:aurora"` (pattern `orb|cartoon|glb|vrm|procedural`). Update
  `src/features/avatar/types.ts` (additive), the controller/stage selection, `src/lib/types.ts` `model_format` union,
  `src/features/interview/useAvatarController.ts`, `src/features/candidate/systemCheck/AvatarProbe.tsx` and the admin
  `src/features/recruiter/admin/settings/InterviewerSettings.tsx`.
- Admin "Interviewer presence": palette choice with a live animated preview, and a "Preview voice" button that plays the
  existing `POST /api/v1/settings/voice-preview` audio through the preview so the admin sees the speaking reaction. Keep the
  realistic 3D avatar (`glb`) selectable as a secondary option labelled as such; **do not expose the cartoon character in
  the UI**. Keep the cartoon code only if it typechecks and its tests pass; otherwise remove the unfinished cartoon files
  you created (they are yours; leave everything else).
- Keep `data-testid="avatar-stage"` and `data-avatar-mode` (`3d` / `2d` / `none`) so the E2E spec keeps working.
- `PresenceLine` in `features/interview/components/Stage.tsx`: avoid two competing voice indicators. Keep it only if it
  complements the orb; otherwise remove it or tone it down, and keep its tests green.
- Public API (`src/features/avatar/index.ts`) stays backwards compatible.

## Ownership
`apps/web/src/features/avatar/**`, `apps/web/public/avatars/**`, `apps/web/avatar-dev.html`, the integration files listed
above, `apps/web/src/features/interview/components/Stage.tsx`, and `AVATAR_SYSTEM.md` (rewrite it as "Interviewer
presence": orb default, how speaking sync works, fallbacks, the optional 3D avatar).

## Quality bar and verification
- Iterate visually with Playwright on the GPU (`--use-angle=gl-egl --ignore-gpu-blocklist --enable-gpu`): every state,
  all three palettes, light and dark backdrops, desktop + mobile + compact sizes, and frame strips (8-12 frames) of a real
  TTS fixture being spoken so the syllable sync is visible. Read every screenshot as an art director; it must look
  premium next to the best voice-assistant orbs you know. Save the final images under
  `/tmp/claude-1000/-home-techsphere-Documents-project-Interview/701114dc-1458-4aa1-a843-dbecc46944b2/scratchpad/orb/` and list
  the best ones in your report.
- Measure fps, GPU frame time, bundle impact.
- Tests: add vitest tests (palette parsing, state mapping, amplitude envelope attack/release, viseme articulation,
  reduced motion, 2D fallback selection); run `npx vitest run src/features/avatar src/features/interview`,
  `npm run typecheck`, `npm run test`, `npm run build`.
- Update your memory note (avatar note or a new presence note) and the MEMORY.md index line.
