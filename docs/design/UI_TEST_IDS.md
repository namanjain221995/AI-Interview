# UI test-id contract

Written for: frontend engineers and the end-to-end test suite (`tests/e2e`). Every element below must carry the given
`data-testid` so Playwright can drive the real candidate and recruiter flows. Test ids are stable API: do not rename them.

## Candidate journey

| Screen | Element | data-testid |
|---|---|---|
| Invite entry `/i/:token` | email input | `invite-email-input` |
| | access code input (when required) | `invite-access-code-input` |
| | verify button | `invite-verify-button` |
| | error message | `invite-error` |
| Welcome | continue | `welcome-continue` |
| Instructions | continue | `instructions-continue` |
| Consent | one checkbox per disclosure item (`key` from the Disclosure) | `consent-item-<key>` |
| | accept button | `consent-accept-button` |
| Resume | file input (`<input type="file">`, may be visually hidden) | `resume-file-input` |
| | processing status (text includes the current step) | `resume-status` |
| | parsed summary panel, with `data-stage="quick|full"` (quick = the instant first read, full = the checked profile) | `resume-summary` |
| | "First read" badge while the profile is still being checked | `resume-profile-stage` |
| | per-section mark (`basics`, `experience`, `skills`, `education`, `interview`) with `data-state="quick|active|done|fallback"` | `resume-section-<key>` |
| | note on a scanned résumé while later pages are still read ("From page 1 of 2") | `resume-partial-note` |
| | continue (enabled once the text is read; the profile finishes in the background) | `resume-continue` |
| Round select | one card per round | `round-option-<roundId>` |
| | continue | `round-continue` |
| System check | start checks | `syscheck-run` |
| | per-check status (`camera`, `microphone`, `speakers`, `browser`, `network`, `avatar`, `screen`) with `data-state="pending|running|pass|warn|fail"` | `syscheck-status-<check>` |
| | continue | `syscheck-continue` |
| Interview | begin button | `interview-begin-button` |
| | root element with `data-turn-state` and `data-session-status` attributes mirroring the server | `interview-room` |
| | avatar stage with `data-avatar-mode="3d|2d|none"` | `avatar-stage` |
| | remaining-time timer | `interview-timer` |
| | interviewer subtitle (latest utterance text) | `subtitle-interviewer` |
| | candidate live transcript | `subtitle-candidate` |
| | done answering | `done-answering-button` |
| | repeat question | `repeat-question-button` |
| | open typed answer | `type-answer-button` |
| | typed answer textarea / submit | `typed-answer-input` / `typed-answer-submit` |
| | end interview / confirm | `end-interview-button` / `end-interview-confirm` |
| | connection indicator with `data-state="connected|reconnecting|offline"` | `connection-status` |
| | notices area | `interview-notice` |
| Coding | workspace root | `coding-workspace` |
| | Monaco container | `coding-editor` |
| | language select | `coding-language-select` |
| | run tests / submit | `coding-run-tests` / `coding-submit` |
| | request hint | `coding-hint-button` |
| | hints remaining text, exactly `Hints remaining: N` | `coding-hints-remaining` |
| | hint N panel | `coding-hint-<level>` |
| | results panel with `data-status` of the last run | `coding-results` |
| Complete | page root | `complete-page` |
| | link to report (when available) | `report-link` |
| Candidate report | root / overall score | `candidate-report` / `report-overall-score` |

## Staff

| Screen | Element | data-testid |
|---|---|---|
| Login | email / password / submit / error | `login-email` / `login-password` / `login-submit` / `login-error` |
| Sessions list | row per session (`data-session-id`) | `session-row` |
| Session detail | tab triggers | `session-tab-overview`, `session-tab-transcript`, `session-tab-recording`, `session-tab-questions`, `session-tab-competencies`, `session-tab-timeline`, `session-tab-coding`, `session-tab-integrity`, `session-tab-plan`, `session-tab-review` |
| | overall score | `session-overall-score` |
| | recommendation badge | `session-recommendation` |
| | transcript entries | `transcript-entry` |
| | competency card (`data-competency-key`) | `competency-card` |
| | evidence item | `evidence-item` |
| | timeline chart | `timeline-chart` |
| | integrity event row | `integrity-event` |
| | review decision submit | `review-submit` |
| | Recording tab: root / no-recording state / consent notice / processing callout / process now | `recording-tab` / `recording-empty` / `recording-notice` / `recording-processing` / `recording-finalize` |
| | Recording tab: player card / camera video / shared-screen video / shared-screen toggle (`aria-pressed`) / download (admins only) | `recording-player` / `recording-video` / `recording-screen-video` / `recording-screen-toggle` / `recording-download` |
| | Recording tab: timeline / integrity observation marker / transcript panel / transcript line (`data-active="true"` while heard) / part buttons (`aria-pressed`) | `recording-timeline` / `recording-marker` / `recording-transcript` / `recording-line` / `recording-part-{n}`, `recording-screen-part-{n}` |
| Recruiter report | root | `recruiter-report` |
| Candidate detail, Profile tab | banner on the instant first read (profile `status="quick"`) while the AI profile is generated | `profile-quick-banner` |
| Job detail | invite button / invite link output / access code output | `invite-candidate-button` / `invite-link` / `invite-access-code` |
| Staff shell (below `lg`) | menu button opening the navigation drawer (`aria-expanded`) / the drawer (`role="dialog"`) | `staff-nav-toggle` / `staff-nav-drawer` |

## App-wide

| Screen | Element | data-testid |
|---|---|---|
| Staff top bar, candidate header, sign-in page, `/dev/ui` | theme menu button with `data-theme-preference` (`light`, `dark` or `system`) and `data-resolved-theme` (`light` or `dark`) | `theme-toggle` |
| | theme choices (`role="menuitemradio"`, `aria-checked`) | `theme-option-light`, `theme-option-dark`, `theme-option-system` |

The theme is also readable on `<html>`: `data-theme` (`light` or `dark`) and `data-theme-preference`. Tests can preset it without
the menu: `localStorage.setItem("app.theme", "dark")` in an init script (values `light`, `dark`, `system`). The interview
room stays dark in every theme.
