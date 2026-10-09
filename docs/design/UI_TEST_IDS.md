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
| Intake welcome (`round_type: intake`) | overview root (seven parts, privacy, who decides; never scores) | `intake-overview` |
| | proportional seven-part strip | `intake-parts-strip` |
| Intake instructions | root | `intake-instructions` |
| Intake details `/candidate/:interviewId/details` | page root / form | `intake-details-page` / `intake-details-form` |
| | one control per field: `full_name`, `target_role`, `target_role_other`, `open_to_ai_ml`, `technical_background`, `primary_language`, `primary_language_other`, `self_rating`, `scripting`, `scripting_details`, `data_ml_libraries`, `years_hands_on`, `experience_level_comfort`, `training_commitment`, `weekly_hours`, `availability`, `available_from`, `accuracy_confirmed`, `work_authorized`, `needs_sponsorship` (the last two only when the organisation asks) | `intake-field-<name>` |
| | one option of a choice field (radio), e.g. `intake-field-target_role-ai_ml`, `intake-field-self_rating-7` | `intake-field-<name>-<value>` |
| | error message of a field | `intake-field-error-<name>` |
| | save and continue (Continue when locked) / save error | `intake-details-submit` / `intake-details-error` |
| Intake résumé review `/candidate/:interviewId/resume-review` | root with `data-stage="none|quick|full"` | `intake-resume-review` |
| | "First read" / "Checked" badge | `resume-review-stage` |
| | section (`roles`, `projects`, `skills`, `languages`, `tools`, `education`, `certifications`) | `resume-review-section-<section>` |
| | item (roles/projects/education: the item `ref`, e.g. `r1`; list values: `<section>-<index>`, e.g. `skills-0`; headline and years: `summary`) | `resume-review-item-<ref>` |
| | edit / remove an item | `resume-review-edit-<ref>` / `resume-review-remove-<ref>` |
| | add to a section / save an item form | `resume-review-add-<section>` / `resume-review-item-save` |
| | "Looks right, continue" / "Continue without reviewing" (after 3 minutes of processing) / save error | `resume-review-confirm` / `resume-review-skip` / `resume-review-error` |
| Interview (intake) | stage indicator with `data-stage` and `data-stage-number` / seven-part strip | `intake-stage-indicator` / `intake-stage-strip` |
| | the room root also carries `data-activity-kind` and `data-activity-phase` (`armed` before the first phase) while an exercise is up | `interview-room` |
| | exercise layout / cue under the interviewer | `activity-stage` / `activity-cue` |
| | activity panel with `data-kind="reading_aloud|read_relay|topic_card"`, `data-phase="read|relay|prepare|speak|none"`, `data-status="armed|running|between|ended"` | `activity-panel` |
| | title / passage (`lang="en"`) / topic card points / body with `data-page="dark|light"` | `activity-title` / `activity-passage` / `activity-card-bullets` / `activity-body` |
| | countdown with `data-remaining-s` | `activity-countdown` |
| | finish button ("Finish reading", "I'm ready", "Start now", "I'm done") | `activity-finish-button` |
| | "The passage is hidden" notice (read-and-relay) | `activity-hidden-notice` |
| | microphone and recording status with `data-capture="true|false"` | `activity-mic-status` |
| | text size / light page (`aria-pressed`) | `activity-font-smaller`, `activity-font-larger` / `activity-contrast-toggle` |
| | polite announcements (phase, hidden, 10 seconds left) | `activity-announcer` |

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
| Sessions list | round type filter (sends `round_type`) | `sessions-filter-round-type` |
| Session detail (intake round) | "Open intake review" link / Decision tab pointer replacing the generic decision form | `session-intake-review-link` / `session-intake-decision-pointer` |
| Intake list `/app/intake` | page root / create button / feature-off state | `intake-page` / `intake-create-button` / `intake-disabled` |
| | stats row / one figure (`invitations`, `started`, `completed`, `incomplete`, `awaiting_review`, `average_score`, `average_duration`) | `intake-stats` / `intake-stat-<key>` |
| | bucket view (`all`, `drafts`, `active`, `scheduled`, `in_progress`, `completed`, `awaiting_review`, `accepted`, `borderline`, `rejected`, `training`, `expired`, `cancelled`; `role="radio"`) | `intake-bucket-<key>` |
| | search / job / target role / AI band / reviewer filters / sort | `intake-search` / `intake-filter-job` / `intake-filter-role` / `intake-filter-band` / `intake-filter-reviewer` / `intake-sort` |
| | row with `data-intake-id` and `data-bucket` / previous and next page | `intake-row` / `intake-page-prev`, `intake-page-next` |
| Intake create `/app/intake/new` (`?mode=batch`, `?draft=<id>`) | page root / one candidate or batch | `intake-create-page` / `intake-mode-single`, `intake-mode-batch` |
| | candidate search / a search result / the chosen candidate / new candidate name and email | `intake-candidate-search` / `intake-candidate-option` / `intake-candidate-selected` / `intake-candidate-name`, `intake-candidate-email` |
| | job / target role / résumé file input / chosen résumé | `intake-job-select` / `intake-target-role` / `intake-resume-upload` / `intake-resume-file` |
| | duration slider / voice orb / reconnections / link expiry / earliest start / access code / résumé required | `intake-duration` / `intake-presence-toggle` / `intake-max-reconnects` / `intake-expiry` / `intake-schedule` / `intake-access-code-toggle` / `intake-resume-required` |
| | work authorisation mode (radio group) / country / assigned reviewer | `intake-mq1-mode` / `intake-mq1-country` / `intake-reviewer-select` |
| | save as draft / create invitation | `intake-save-draft` / `intake-send` |
| | result panel / invite link (shown once) and its copy button / access code and its copy button / copy invitation message | `intake-invite-result` / `intake-invite-link`, `intake-copy-link` / `intake-access-code`, `intake-copy-code` / `intake-copy-message` |
| | batch input / preview / row with `data-row` and `data-status` (`valid`, `invalid`; after submit `created`, `failed`) / submit / results / download links (CSV) | `intake-batch-input` / `intake-batch-preview` / `intake-batch-row` / `intake-batch-submit` / `intake-batch-results` / `intake-batch-download` |
| Intake review `/app/intake/:intakeId` (`?tab=`, `?q=`, `?a=`, `?version=`) | page root with `data-intake-id` / tab triggers | `intake-review-page` / `intake-tab-overview`, `intake-tab-stages`, `intake-tab-reading`, `intake-tab-topic`, `intake-tab-scorecard`, `intake-tab-recording`, `intake-tab-integrity`, `intake-tab-decision` |
| | re-evaluate / printable report link | `intake-reevaluate` / `intake-report-link` |
| | Overview: root / eligibility block (human review only) / candidate profile link / stage row with `data-status` / review item with `data-kind` | `intake-overview-tab` / `intake-eligibility` / `intake-resume-link` / `intake-stage-row-<key>` / `intake-review-item` |
| | Stages: root / question and answer with `data-question-id` / play in recording / its video | `intake-stages-tab` / `intake-qa-item` / `intake-play-recording` / `intake-recording-video` |
| | evidence citation with `data-supports` (`positive`, `negative`, `context`), anywhere on the page | `intake-evidence-ref` |
| | Reading: root / Drill A / Drill B / passage alignment with `data-level` (`word`, `sentence`, `none`; marks carry `data-mark`) | `intake-reading-tab` / `intake-reading-drill-a` / `intake-reading-drill-b` / `intake-alignment` |
| | Reading: metric (`completeness`, `wer`, `duration`, `wpm`, `pauses`, `long_pauses`, `sentences`, `changes`) / pronunciation note / formula line / relay concept with `data-status` | `intake-reading-metric-<name>` / `intake-pronunciation` / `intake-technical-reading` / `intake-relay-concept` |
| | Topic: root / card / bullet with `data-status` (`covered`, `partial`, `missing`, `unknown`) | `intake-topic-tab` / `intake-topic-card` / `intake-bullet-coverage-<n>` |
| | Scorecard: root / competency row with `data-score` and `data-status` / weighted total with `data-total` / out of 100 / band with `data-band` / automatic checks | `intake-scorecard` / `intake-competency-<key>` / `intake-weighted-total` / `intake-normalized-100` / `intake-band` / `intake-validator-issues` |
| | Decision: form / outcome (`accept`, `accept_with_notes`, `second_interview`, `reject`, `request_evidence`) / override warning / rationale / review item resolution / submit | `intake-decision-form` / `intake-decision-outcome-<outcome>` / `intake-override-warning` / `intake-decision-rationale` / `intake-review-item-resolution-<id>` / `intake-decision-submit` |
| | Decision: history / entry with `data-outcome` | `intake-decision-history` / `intake-decision-entry` |
| Intake report `/app/reports/:sessionId` (intake sessions) | root (`data-report-root`; sections carry `data-section` A-K) | `intake-report` |
| Intake analytics `/app/intake/analytics` | page root / period / KPI row | `intake-analytics-page` / `intake-analytics-period` / `intake-analytics-kpis` |
| Intake settings `/app/intake/settings` | page root / approved information row / save | `intake-settings-page` / `intake-approved-info-row` / `intake-settings-save` |
| Round editor (intake round) | stage plan strip / Intake tab panel | `intake-round-blueprint` / `intake-round-panel` |

## App-wide

| Screen | Element | data-testid |
|---|---|---|
| Staff top bar, candidate header, sign-in page, `/dev/ui` | theme menu button with `data-theme-preference` (`light`, `dark` or `system`) and `data-resolved-theme` (`light` or `dark`) | `theme-toggle` |
| | theme choices (`role="menuitemradio"`, `aria-checked`) | `theme-option-light`, `theme-option-dark`, `theme-option-system` |

The theme is also readable on `<html>`: `data-theme` (`light` or `dark`) and `data-theme-preference`. Tests can preset it without
the menu: `localStorage.setItem("app.theme", "dark")` in an init script (values `light`, `dark`, `system`). The interview
room stays dark in every theme.
