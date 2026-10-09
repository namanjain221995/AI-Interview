# Wave C brief: avatar visual + lip-sync polish

Read `wave-c-common.md` first. Load the "frontend-design" skill.

YOU OWN: `apps/web/src/features/avatar/**`, `apps/web/scripts/optimize-avatar.mjs`, `apps/web/public/avatars/**`,
`apps/web/avatar-dev.html`.

Current state: CC0 MPFB interviewer renders at 60 fps on the GB10 GPU; outfit restyled to a navy crew-neck knit; known
issues: thin strand eyebrows/eyelashes look like scribbles at low pixel ratio, skin slightly pale/grey, framing leaves a
lot of empty space above the head, outfit reads casual (a T-shirt), gestures only seen in playground.

1. Make the interviewer look professional and warm: soft painted brow shape (or better brow material), warmer natural skin
   shading, a more formal-reading outfit (e.g. texture work that reads as a dark blazer over a light shirt, or a convincing
   smart knit with collar — within the asset's geometry), tasteful studio background, bust framing that fills the stage.
2. Verify lip-sync with REAL TTS output: fetch a few utterances from the running TTS service (`POST
   http://127.0.0.1:8210/v1/tts`, returns PCM16 base64 + Oculus visemes) into dev fixtures, add a playground mode that plays
   them with the real viseme timeline through the same scheduling path the interview room uses, record frame strips and
   check mouth shapes against the audio (PP/FF closures visible, no jitter, mouth closes in pauses).
3. Check every state/gesture transition for snapping, hand clipping into the body, and eye contact; keep 60 fps on GPU and
   the 2D fallback working. Keep the public API (`index.ts`, `types.ts`) backwards compatible.
4. Screenshots: render with Playwright + `--use-angle=gl-egl --ignore-gpu-blocklist --enable-gpu`, inspect images, iterate.
   Run `npx vitest run src/features/avatar`, `npm run typecheck` (filter to your files if others are mid-edit) and confirm
   the optimized GLB stays <= 8 MB.
