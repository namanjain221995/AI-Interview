# Wave C brief: security review (REVIEW ONLY — do not edit source files)

Read `wave-c-common.md` first. You own NO source files: other Wave C agents are editing code in parallel. Produce a
precise, prioritized security assessment that the integrator can apply. You MAY write scratch scripts outside the repo
(in /tmp/claude-*/ scratch dirs) and run read-only probes against the live stack and against your own throwaway API
instance (port 8400-8499).

Scope (whole repository):
1. AuthN/AuthZ: JWT handling, refresh rotation/reuse, cookies flags, CSRF double-submit coverage (every unsafe route),
   RBAC matrix enforcement on EVERY router (enumerate all routes from the OpenAPI schema and verify each has the right
   dependency), organization isolation (IDOR across orgs on every resource id: jobs, rounds, candidates, resumes,
   interviews, sessions, reports, evidence, coding, integrity events, media), candidate principal scoping (a candidate can
   only reach their own interview/session/report; test with two candidates), staff vs candidate token confusion.
2. Invite flow: token entropy/hash storage, brute force/lockout, access code, enumeration via responses/timing.
3. Uploads: resume validation bypasses (polyglots, zip bombs, PDF active content, oversized images), frame uploads,
   recording chunks, filename handling, storage path traversal, media signed URL forgery/expiry/replay, content-type sniffing.
4. WebSocket: ticket single-use, origin validation, message validation/size/rate limits, binary frame abuse, cross-session
   access, resource exhaustion (many sessions, audio flooding).
5. Code sandbox: review services/sandbox isolation flags + HMAC; confirm candidate code cannot reach the network, the API,
   docker.sock or secrets; review API-side redaction of hidden tests in every coding response (REST + WS).
6. Prompt injection: candidate-controlled text (resume, answers, code) reaching LLM prompts — check fencing
   (`untrusted` filter) is used everywhere, check that model output cannot trigger privileged actions, and that report
   narratives cannot inject HTML (Jinja autoescape, frontend markdown rendering without raw HTML).
7. Web: CSP/headers at the edge, XSS sinks in React (dangerouslySetInnerHTML, markdown), open redirects (login `next`),
   localStorage secrets, error messages leaking internals.
8. Secrets/config: secrets in repo files, logs (grep logs of running containers for tokens/passwords/cookies), Docker
   images (env baked?), default credentials, `ENABLE_TEST_HOOKS` exposure (edge blocks LAN; is it on in .env? note it as
   dev-only), production validators in config.
9. SSRF / outbound: any user-influenced URL fetched server-side (TTS voice preview, avatar model_url setting, OCR), webhook-like features.
10. Data protection: retention sweep correctness, consent enforcement before recording/frames, PII in model_calls ledger,
    audit coverage of sensitive reads (reports, transcripts, media).
11. Dependency/image hygiene: known-vulnerable versions you can detect offline (pip list / npm ls versions vs your
    knowledge), containers running as root, docker socket exposure.

Method: read code, enumerate routes programmatically, write targeted probe scripts (httpx/websockets) against a throwaway
API instance with two orgs/two candidates, and confirm each finding with evidence. Do not run destructive load tests
against the shared model endpoints.

Final answer: FINDINGS ranked (critical/high/medium/low/info), each with: title, affected file:line, evidence (exact probe
and result), impact, and the exact recommended fix (code-level). Also list verified-good controls briefly.
