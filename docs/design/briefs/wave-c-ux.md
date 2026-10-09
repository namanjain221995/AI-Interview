# Wave C brief: product UX + visual polish of the web app

Read `wave-c-common.md` first. Load the "frontend-design" skill (Skill tool) and the "webapp-testing" skill before starting.

YOU OWN: `apps/web/src/**` EXCEPT `src/features/avatar/**` (avatar agent) and `src/lib/realtime/protocol.ts` (contract).
Also `apps/web/index.html`, `apps/web/vite.config.ts`, `apps/web/public/**` except `public/avatars/**`.

Goal: every screen of the candidate app and the staff app looks and behaves like a premium, calm, trustworthy enterprise
product, on real data, with no broken states.

1. Bring up a Vite dev server on a spare port (5200-5299) proxying to the LIVE API (vite.config already proxies /api and
   /ws to 127.0.0.1:8200), log in as each role and walk EVERY route with Playwright (headless chromium with
   `--use-angle=gl-egl --ignore-gpu-blocklist --enable-gpu` so the 3D avatar renders on the GB10 GPU). Create your own
   data through the UI/API as needed (a candidate + invite; use the sample resumes in samples/resumes). Screenshot every
   page/state (desktop 1440x900 and mobile 390px for candidate pages), read the images, fix everything that looks off:
   spacing, hierarchy, alignment, truncation, empty/loading/error states, inconsistent components, tone of copy
   (integrity = observations not accusations; recommendation = assistive, human decides), accessibility (labels, focus
   order, keyboard, contrast, aria-live for turn changes and subtitles, reduced motion).
2. Candidate flow specifics: the resume step lets candidates continue after text extraction while the profile is
   generated in the background (~3 min) — make the background progress elegant and make the interview Begin gate
   ("Preparing your interview…") clear and reassuring; the server now pushes `session.state` with status live at start and
   a `notice` code `preparing` when the engine defers the start until the profile is ready (it then starts automatically on
   `session.ready`) — handle that gracefully. Interview room: large avatar stage (bust framing), clean subtitles, clear
   turn cues (listening / thinking / speaking), unobtrusive controls, honest monitoring notice, smooth coding-mode layout.
3. Known issues to fix: `FileDropzone` needs input props passthrough so `data-testid` goes on the `<input type=file>` (then
   remove ResumePage's effect workaround); update `lib/types.ts` mirrors for `Disclosure` (items list + consent_items map +
   required_items + consented), `PortalResume`, `SystemCheckRequest` to the real portal DTOs (see
   `apps/api/app/schemas/api/portal.py`); the Monaco chunk is 3.87 MB — load only the languages/workers needed
   (javascript, typescript, python, java, go, cpp, sql; editor + ts/json workers at most) and keep `optimizeDeps` in sync so
   the dev server never re-optimizes mid-session; verify after `npm run build` that only `vendor` is modulepreloaded on
   index.html.
4. Keep every `data-testid` from `docs/design/UI_TEST_IDS.md` (add any missing). Run `npm run typecheck`, `npm run test`,
   `npm run build` until green. Do not change backend code; list backend issues under REQUESTS FOR OTHER OWNERS with exact
   endpoint + payload evidence.
