"""Consent disclosure: exactly what the candidate is told before anything is
recorded or analysed.

``build_disclosure`` turns the frozen policy snapshot into plain-language items
(microphone, camera, transcript, integrity monitoring, camera snapshot
analysis, on-device face check, screen sharing, recordings, AI assessment,
retention), a purpose
statement, the human-decision statement and an honest limitations statement.
The text is fully deterministic (no clock, no randomness): the same snapshot,
organization name and job title always produce the same text and the same
``policy_hash`` = sha256 over the canonical JSON of the rendered text plus the
normalised snapshot. The portal echoes the hash back with the consent, so the
stored consent proves which text was shown.

Consequences of stopping a required screen share and any recruiter-configured
pause/terminate rules are derived from the same effective rules the
integrity service enforces (``app.services.integrity.rules``), so what the
candidate reads is what the platform does.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from app.domain.enums import IntegrityAction, IntegrityEventType
from app.schemas.policies import PolicySnapshot
from app.services.integrity.contracts import Disclosure, DisclosureItem
from app.services.integrity.policy import canonical_json, parse_policy_snapshot
from app.services.integrity.rules import (
    CLIENT_EVENT_TYPES,
    POLICY_ACTIONS,
    SERVER_EVENT_TYPES,
    EffectiveRule,
    label_for,
    resolve_rules,
)

T = IntegrityEventType
A = IntegrityAction

LIMITATIONS_CORE = (
    "A web browser cannot lock your computer; fullscreen and focus are monitored, not enforced."
)
ITEM_KEYS: tuple[str, ...] = (
    "microphone",
    "camera",
    "transcript",
    "integrity_monitoring",
    "camera_frames",
    "face_check",
    "screen_share",
    "audio_recording",
    "video_recording",
    "ai_assessment",
    "retention",
)
_MAX_NAME = 200
_SPACE = re.compile(r"\s+")
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


def _clean_name(value: str | None, fallback: str) -> str:
    text = _SPACE.sub(" ", (value or "").replace("\x00", "")).strip()
    return text[:_MAX_NAME] if text else fallback


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _join(parts: list[str]) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def _list_sentence(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _list_items(items: list[str]) -> str:
    """Semicolon-separated list (items may contain commas)."""
    if len(items) <= 1:
        return "".join(items)
    return "; ".join(items[:-1]) + "; and " + items[-1]


def _connection_text(integ: Any) -> str:
    parts = [
        "Connection drops, reconnections and page reloads are logged so the interview can resume correctly."
    ]
    if not integ.allow_page_reload_resume:
        parts.append("If you reload or close the page, the interview cannot be resumed.")
    parts.append(
        f"If the connection drops more than {integ.max_reconnects} "
        f"{'time' if integ.max_reconnects == 1 else 'times'}, the interview may not be resumable."
    )
    return " ".join(parts)


def _consequence(rule: EffectiveRule) -> str:
    if rule.action is A.TERMINATE_INTERVIEW:
        return "the interview ends"
    return "the interview pauses until the condition is resolved"


def _screen_stop_text(rule: EffectiveRule) -> str:
    if not rule.enabled or rule.action is A.LOG_ONLY:
        return "If you stop sharing, the stop is noted in the session log."
    if rule.action is A.TERMINATE_INTERVIEW:
        return (
            "If you stop sharing, the interview ends, as required by the hiring organization, "
            "and the stop is noted for the reviewer."
        )
    if rule.action is A.PAUSE_INTERVIEW:
        return (
            "If you stop sharing, the interview pauses until you share your entire screen again, "
            "and the stop is noted for the reviewer."
        )
    return "If you stop sharing, you will be asked to share again, and the stop is noted for the reviewer."


def face_check_enabled(snap: PolicySnapshot) -> bool:
    """The on-device face check applies: monitoring on, the camera used and the
    policy toggle on (same condition as the face-check rules)."""
    integ = snap.integrity
    return bool(
        integ.enabled and (integ.camera_required or integ.camera_frame_analysis) and integ.face_presence_check
    )


def _build_items(
    snap: PolicySnapshot, rules: Mapping[IntegrityEventType, EffectiveRule], *, organization: str, job: str
) -> list[DisclosureItem]:
    integ, rec, ret = snap.integrity, snap.recording, snap.retention
    on = integ.enabled
    frames_on = on and integ.camera_frame_analysis
    face_on = face_check_enabled(snap)
    camera_on = on and (integ.camera_required or integ.camera_frame_analysis)
    share_on = on and (integ.screen_share_required or integ.screen_share_analysis)
    items: list[DisclosureItem] = []

    # 1. Microphone ------------------------------------------------------------
    items.append(
        DisclosureItem(
            key="microphone",
            title="Microphone",
            description=_join(
                [
                    "Your microphone is used so you can answer the interviewer by voice.",
                    "While you speak, your audio is sent to the platform's speech-recognition service.",
                    "If speaking is difficult, you can type an answer instead."
                    if rec.allow_typed_answers
                    else "",
                ]
            ),
            required=True,
            enabled=True,
            summary="You answer by voice; what you say is turned into text."
            + (" You can also type an answer." if rec.allow_typed_answers else ""),
        )
    )

    # 2. Camera ------------------------------------------------------------------
    if camera_on or rec.record_video:
        camera_parts = [
            "Your camera must stay on for the whole interview."
            if (on and integ.camera_required)
            else "Your camera is used during the interview.",
            "You will see your own camera preview.",
            "Your browser checks on your device that one face is visible, as described under On-device face check."
            if face_on
            else "",
            "Occasional still images are analysed as described under Camera snapshot analysis."
            if frames_on
            else "Camera images are not analysed by AI.",
            "Your camera video is recorded, as described under Video recording."
            if rec.record_video
            else "Your camera video is not recorded.",
            # Recording alone never makes the camera mandatory: without it only the sound is recorded.
            "If you do not allow the camera, only the sound of the interview is recorded."
            if rec.record_video and not (on and integ.camera_required)
            else "",
        ]
        items.append(
            DisclosureItem(
                key="camera",
                title="Camera",
                description=_join(camera_parts),
                required=bool(on and integ.camera_required),
                enabled=True,
                summary=(
                    "Your camera stays on for the whole interview"
                    if (on and integ.camera_required)
                    else "Your camera is used during the interview"
                )
                + ("; the video is recorded." if rec.record_video else "."),
            )
        )
    else:
        items.append(
            DisclosureItem(
                key="camera",
                title="Camera",
                description="Your camera is not used for this interview.",
                required=False,
                enabled=False,
                summary="Your camera is not used.",
            )
        )

    # 3. Transcript ----------------------------------------------------------------
    items.append(
        DisclosureItem(
            key="transcript",
            title="Transcript",
            description=_join(
                [
                    "Everything said in the interview, by you and by the AI interviewer, is converted to text.",
                    "Typed answers and code you write in the editor are saved with it.",
                    "The transcript is used to evaluate your answers. It stays in the hiring team's records until "
                    "they delete it, and you can ask them to delete it at any time.",
                ]
            ),
            required=True,
            enabled=True,
            summary="Everything said in the interview is turned into text and used to evaluate your answers.",
        )
    )

    # 4. Integrity monitoring (browser) -----------------------------------------------
    monitored: list[str] = []
    if on and integ.require_fullscreen:
        monitored.append("leaving fullscreen mode")
    if on and integ.monitor_focus:
        monitored.append("switching to another tab, window or application (and for how long)")
        monitored.append("whether more than one display is connected")
    if on and integ.monitor_clipboard:
        monitored.append(
            "copy and paste actions (only that they happened and their size, never what was copied or pasted)"
        )
    if on and integ.monitor_shortcuts:
        monitored.append(
            "use of blocked keyboard shortcuts (such as developer tools or printing) and of the right-click menu"
        )
    if on and (integ.camera_required or integ.camera_frame_analysis):
        monitored.append("your camera or microphone being turned off")
    if monitored:
        policy_rules = [
            rule
            for event_type, rule in sorted(rules.items(), key=lambda item: item[0].value)
            if rule.enabled
            and rule.action in POLICY_ACTIONS
            and event_type in (CLIENT_EVENT_TYPES | SERVER_EVENT_TYPES)
            and event_type is not T.SCREEN_SHARE_STOPPED  # explained under Screen sharing
        ]
        if policy_rules:
            consequences = "; ".join(
                f"{label_for(rule.event_type).lower()}: {_consequence(rule)}" for rule in policy_rules
            )
            consequence_text = (
                f"Under this interview's rules: {consequences}. "
                "Every other event is only noted for a human reviewer and never ends the interview on its own."
            )
        else:
            consequence_text = (
                "These events are noted as observations for a human reviewer. On their own, they never pause or "
                "end the interview."
            )
        integrity_text = _join(
            [
                f"While the interview is open, your browser reports these events: {_list_items(monitored)}.",
                "If one of these happens, you may see a short reminder on screen."
                if integ.warn_candidate
                else "",
                consequence_text,
                "Pasting into the code editor is turned off for this interview."
                if (on and integ.monitor_clipboard and integ.block_paste_in_coding)
                else "",
                _connection_text(integ),
            ]
        )
        items.append(
            DisclosureItem(
                key="integrity_monitoring",
                title="Integrity monitoring",
                description=integrity_text,
                required=True,
                enabled=True,
                summary=_integrity_summary(integ, has_policy_actions=bool(policy_rules)),
            )
        )
    else:
        items.append(
            DisclosureItem(
                key="integrity_monitoring",
                title="Integrity monitoring",
                description=(
                    "Browser activity monitoring is turned off for this interview. " + _connection_text(integ)
                ),
                required=False,
                enabled=False,
                summary="Browser activity is not monitored.",
            )
        )

    # 5. Camera snapshot analysis --------------------------------------------------------
    if frames_on:
        storage_text = (
            f"Snapshots linked to an observation are kept for {_days(ret.frame_days)} so a person can review them; "
            "all other snapshots are deleted right after analysis."
            if integ.store_evidence_frames
            else "Snapshots are deleted right after analysis; none are kept."
        )
        items.append(
            DisclosureItem(
                key="camera_frames",
                title="Camera snapshot analysis",
                description=_join(
                    [
                        f"About every {integ.camera_frame_interval_seconds} seconds, a still image from your camera "
                        "is analysed by an AI model.",
                        "It records only objective facts about the scene: whether a person is visible, whether more "
                        "than one person is visible, whether a phone-like object is visible, and whether the camera is "
                        "covered or too dark.",
                        "A face detector also counts the faces in each image; a face that does not move, such as a "
                        "photo or a poster, is not counted.",
                        "It never identifies you and never assesses your appearance, facial expressions, emotions or "
                        "any personal characteristic.",
                        "Where you are looking is never treated as a concern on its own.",
                        storage_text,
                        "Observations from camera images never pause or end the interview; they are only shown to a "
                        "human reviewer.",
                    ]
                ),
                required=True,
                enabled=True,
                summary=f"About every {integ.camera_frame_interval_seconds} seconds an AI model checks a camera "
                "snapshot for objective facts only, such as nobody in view, a second person or a phone.",
            )
        )
    else:
        items.append(
            DisclosureItem(
                key="camera_frames",
                title="Camera snapshot analysis",
                description="Camera images are not analysed for this interview.",
                required=False,
                enabled=False,
                summary="Camera images are not analysed.",
            )
        )

    # 5b. On-device face check -----------------------------------------------------------
    if face_on:
        face_rule = rules[T.FACE_NOT_VISIBLE]
        multi_rule = rules[T.MULTIPLE_FACES_VISIBLE]
        snapshot_storage = (
            f"Snapshots linked to an observation are kept for {_days(ret.frame_days)} so a person can review them; "
            "all other snapshots are deleted right after the check."
            if integ.store_evidence_frames
            else "Snapshots are deleted right after the check; none are kept."
        )
        items.append(
            DisclosureItem(
                key="face_check",
                title="On-device face check",
                description=_join(
                    [
                        "While the interview is open, your browser analyses your camera picture on your own device, "
                        "a few times a second, to check that one face is visible.",
                        "It only finds where faces are in the picture and how well lit they are, so it can give you "
                        "tips such as moving a little closer or adding light. It never identifies you and never "
                        "assesses your appearance, facial expressions, emotions or any personal characteristic, and "
                        "where you are looking is never treated as a concern on its own.",
                        "For this check, the camera picture is analysed only on your device. Only short notes are "
                        "sent: when no face has been "
                        f"visible for about {_seconds(face_rule.min_duration_seconds)} or more than one face for "
                        f"about {_seconds(multi_rule.min_duration_seconds)}, and how much of the interview one face "
                        "was visible.",
                        "When such a note may need human review, one snapshot from your camera is sent with it so "
                        "the platform's own face detector can double-check it. "
                        "No other images are sent for this check."
                        + (
                            " (Still images are also analysed as described under Camera snapshot analysis.)"
                            if frames_on
                            else ""
                        ),
                        snapshot_storage,
                        "A face that does not move for a while, such as a photo, a poster or a face on a screen, "
                        "is set aside and not counted.",
                        "If you see a reminder, adjusting your camera is enough. These notes never pause or end the "
                        f"interview; a person at {organization} reviews them.",
                    ]
                ),
                required=True,
                enabled=True,
                summary="Your browser checks a few times a second that one face is visible; the picture stays on "
                "your device.",
            )
        )
    else:
        items.append(
            DisclosureItem(
                key="face_check",
                title="On-device face check",
                description="Your camera picture is not checked for faces for this interview.",
                required=False,
                enabled=False,
                summary="Your camera picture is not checked for faces.",
            )
        )

    # 6. Screen sharing ----------------------------------------------------------------
    if share_on or rec.record_screen:
        captured: list[str] = []
        if on and integ.screen_share_analysis:
            captured.append(
                f"about every {integ.screen_frame_interval_seconds} seconds a still image of your screen is analysed "
                "by an AI model to note whether something that could provide answers is visible, for example an AI "
                "chat assistant, a messaging app or a web search"
            )
        if rec.record_screen:
            captured.append(f"your screen is recorded and kept for {_days(ret.video_days)}")
        if not captured:
            captured.append(
                "only whether sharing is active and whether the entire screen is shared; the screen content is not "
                "stored or analysed"
            )
        share_parts = [
            "You will be asked to share your entire screen for the whole interview."
            if (on and integ.screen_share_required)
            else "You may be asked to share your entire screen.",
            f"What is captured: {_list_sentence(captured)}.",
            "Screen images linked to an observation are kept for "
            f"{_days(ret.frame_days)}; all other screen images are deleted right after analysis."
            if (on and integ.screen_share_analysis and integ.store_evidence_frames)
            else "",
            "How to stop: use your browser's Stop sharing button at any time.",
            _screen_stop_text(rules[T.SCREEN_SHARE_STOPPED]),
            "Observations from screen images never pause or end the interview; they are only shown to a human "
            "reviewer."
            if (on and integ.screen_share_analysis)
            else "",
        ]
        items.append(
            DisclosureItem(
                key="screen_share",
                title="Screen sharing",
                description=_join(share_parts),
                required=bool((on and integ.screen_share_required) or rec.record_screen),
                enabled=True,
                summary=(
                    "You share your entire screen for the whole interview"
                    if (on and integ.screen_share_required)
                    else "You may be asked to share your entire screen"
                )
                + ("; still images of it are checked by AI" if (on and integ.screen_share_analysis) else "")
                + ("; it is recorded" if rec.record_screen else "")
                + ".",
            )
        )
    else:
        items.append(
            DisclosureItem(
                key="screen_share",
                title="Screen sharing",
                description="Screen sharing is not used for this interview.",
                required=False,
                enabled=False,
                summary="Screen sharing is not used.",
            )
        )

    # 7. Audio recording ----------------------------------------------------------------
    items.append(
        DisclosureItem(
            key="audio_recording",
            title="Audio recording",
            description=(
                f"Audio of your spoken answers is recorded and kept for {_days(ret.audio_days)} so a reviewer can "
                "listen to it."
                if rec.record_answer_audio
                else "Your audio is not recorded; only the transcript is kept."
            ),
            required=rec.record_answer_audio,
            enabled=rec.record_answer_audio,
            summary=f"Your spoken answers are recorded and kept for {_days(ret.audio_days)}."
            if rec.record_answer_audio
            else "Your audio is not recorded.",
        )
    )

    # 8. Video recording -----------------------------------------------------------------
    video_parts: list[str] = []
    if rec.record_video:
        video_parts.append(
            "The whole interview is recorded on video: your camera picture together with the sound of both "
            "sides of the conversation, your voice and the AI interviewer's voice. If your camera cannot be "
            "recorded, the sound is still recorded."
        )
    if rec.record_screen:
        video_parts.append(f"Your shared screen is recorded and kept for {_days(ret.video_days)}.")
    if rec.record_video:
        video_parts.append(
            f"People at {organization} who review this interview (interviewers, recruiters and administrators) "
            "can watch the recording; only administrators can download it, and every viewing and download is "
            f"logged. The recording is kept for {_days(ret.video_days)} and then deleted automatically."
        )
    items.append(
        DisclosureItem(
            key="video_recording",
            title="Video recording",
            description=_join(video_parts) if video_parts else "No video is recorded.",
            required=bool(video_parts),
            enabled=bool(video_parts),
            summary=(
                f"The interview is recorded on video and kept for {_days(ret.video_days)}; reviewers at "
                f"{organization} can watch it."
                if rec.record_video
                else f"Your shared screen is recorded and kept for {_days(ret.video_days)}."
                if rec.record_screen
                else "No video is recorded."
            ),
        )
    )

    # 9. AI assessment -------------------------------------------------------------------
    items.append(
        DisclosureItem(
            key="ai_assessment",
            title="AI interviewer and assessment",
            description=_join(
                [
                    "An AI interviewer asks the questions and an AI model evaluates your answers against the skills "
                    f"required for {job}.",
                    f"The evaluation is advisory: a person at {organization} reviews it and makes every hiring decision.",
                    "The AI does not consider personal characteristics such as age, gender, ethnicity, accent or "
                    "appearance.",
                ]
            ),
            required=True,
            enabled=True,
            summary=f"An AI interviewer asks the questions and scores your answers; a person at {organization} "
            "makes every hiring decision.",
        )
    )

    # 10. Retention ------------------------------------------------------------------------
    items.append(
        DisclosureItem(
            key="retention",
            title="How long data is kept",
            description=_retention_summary(snap),
            required=True,
            enabled=True,
            summary=_retention_short(snap),
        )
    )
    return items



def _integrity_summary(integ: Any, *, has_policy_actions: bool) -> str:
    """One sentence for the integrity item (its description lists every monitored event)."""
    watched: list[str] = []
    if integ.require_fullscreen:
        watched.append("leaving fullscreen")
    if integ.monitor_focus:
        watched.append("switching tabs or windows")
    if integ.monitor_clipboard:
        watched.append("copying or pasting")
    if integ.camera_required or integ.camera_frame_analysis:
        watched.append("turning off your camera or microphone")
    watched = watched[:3]
    events = (
        "browser events"
        if not watched
        else watched[0]
        if len(watched) == 1
        else ", ".join(watched[:-1]) + ", or " + watched[-1]
    )
    outcome = (
        "Some of them can affect the interview (see Details)."
        if has_policy_actions
        else "They are only noted for a person to review."
    )
    return f"Your browser notes events such as {events}. {outcome}"


def _retention_short(snap: PolicySnapshot) -> str:
    days = sorted({d for _key, _label, d in auto_deleted_items(snap)})
    period = _days(days[0]) if len(days) == 1 else f"{days[0]}-{_days(days[-1])}"
    return (
        f"Stored files are deleted automatically after {period}; your transcript and assessment stay until the "
        "hiring team deletes them."
    )

def _seconds(value: float) -> str:
    n = max(1, int(round(value)))
    return "1 second" if n == 1 else f"{n} seconds"


def auto_deleted_items(snap: PolicySnapshot) -> list[tuple[str, str, int]]:
    """``(class key, label, days)`` of the stored files this interview keeps, each deleted automatically after
    its period (``retention_classes.AUTO_DELETED``; the G3 test holds every key to that registry)."""
    integ, rec, ret = snap.integrity, snap.recording, snap.retention
    found = [("resume_file", "uploaded resume file", ret.resume_days)]
    if rec.record_answer_audio:
        found.append(("answer_audio", "answer audio", ret.audio_days))
    if rec.record_video or rec.record_screen:
        found.append(("video", "video recordings", ret.video_days))
    if (
        integ.enabled
        and integ.store_evidence_frames
        and (integ.camera_frame_analysis or integ.screen_share_analysis or face_check_enabled(snap))
    ):
        found.append(("frames", "images linked to an integrity observation", ret.frame_days))
    return found


def _retention_summary(snap: PolicySnapshot) -> str:
    # Only stored files are deleted on a schedule today (retention.sweep); transcripts, reports and the text
    # read from a resume are database rows without one yet (C23). Promise exactly that (G3, audit E1).
    files = "; ".join(f"{label}: {_days(days)}" for _key, label, days in auto_deleted_items(snap))
    return (
        f"Stored files from this interview are deleted automatically after these periods: {files}. "
        "Your transcript, the written assessment and the details read from your resume stay in the hiring "
        "team's records until they delete them; you can ask them to delete them at any time."
    )


def retention_days_for_consent(snap: PolicySnapshot) -> int:
    """Longest retention period that applies to this interview's data."""
    integ, rec, ret = snap.integrity, snap.recording, snap.retention
    values = [ret.transcript_days, ret.report_days, ret.resume_days]
    if rec.record_answer_audio:
        values.append(ret.audio_days)
    if rec.record_video or rec.record_screen:
        values.append(ret.video_days)
    if (
        integ.enabled
        and integ.store_evidence_frames
        and (integ.camera_frame_analysis or integ.screen_share_analysis or face_check_enabled(snap))
    ):
        values.append(ret.frame_days)
    return max(values)


def _content(disclosure_fields: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in disclosure_fields.items() if key != "policy_hash"}


def compute_policy_hash(content: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    """sha256 over canonical JSON of the rendered disclosure text + the snapshot."""
    payload = canonical_json({"disclosure": dict(content), "snapshot": dict(snapshot)})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_disclosure(
    policy_snapshot: Mapping[str, Any] | None, *, organization_name: str, job_title: str
) -> Disclosure:
    """Deterministic disclosure for a frozen policy snapshot (raises ``ValueError``
    for an invalid snapshot: never show a disclosure we cannot stand behind)."""
    snap = parse_policy_snapshot(policy_snapshot)
    organization = _clean_name(organization_name, "the hiring organization")
    title_text = _clean_name(job_title, "")
    job = f"the {title_text} role" if title_text else "this role"
    rules = resolve_rules(snap.integrity)
    items = _build_items(snap, rules, organization=organization, job=job)
    title = "Before you start: how this interview uses your data"
    intro = (
        f"{organization} uses an AI interviewer for {job}. Before you begin, please read what this "
        "interview collects, why, and for how long. You confirm the required items together at the end."
    )
    purpose = (
        f"The information is used only to run and evaluate this interview for {job} at {organization}, and "
        "to let a human reviewer check that the interview conditions were followed. It is not used to make "
        "automated decisions about you."
    )
    human_review_statement = (
        f"A person at {organization} makes every hiring decision. AI evaluations are advisory, and integrity "
        "observations are signals for that person to review, never conclusions about you or proof of wrongdoing."
    )
    limitations_statement = _join(
        [
            LIMITATIONS_CORE,
            "Automated observations can be wrong, for example poor lighting can look like a covered camera, which is "
            "why a person reviews them.",
            "Not everything can be detected: keyboard switching between applications and other devices in the room "
            "may go unnoticed, and nothing here is treated as proof on its own.",
        ]
    )
    retention_summary = _retention_summary(snap)
    fields: dict[str, Any] = {
        "policy_version": snap.policy_version,
        "title": title,
        "intro": intro,
        "items": [asdict(item) for item in items],
        "retention_summary": retention_summary,
        "purpose": purpose,
        "human_review_statement": human_review_statement,
        "limitations_statement": limitations_statement,
    }
    policy_hash = compute_policy_hash(_content(fields), snap.model_dump(mode="json"))
    return Disclosure(
        policy_version=snap.policy_version,
        policy_hash=policy_hash,
        title=title,
        intro=intro,
        items=items,
        retention_summary=retention_summary,
        purpose=purpose,
        human_review_statement=human_review_statement,
        limitations_statement=limitations_statement,
    )


class DefaultDisclosureBuilder:
    """``DisclosureBuilder`` implementation used by the candidate portal."""

    def build(self, policy_snapshot: dict[str, Any], *, organization_name: str, job_title: str) -> Disclosure:
        return build_disclosure(policy_snapshot, organization_name=organization_name, job_title=job_title)


def _md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def render_disclosure_markdown(disclosure: Disclosure) -> str:
    """Markdown rendering of exactly the hashed text (for ``text_markdown``)."""
    lines = [f"# {_md(disclosure.title)}", "", _md(disclosure.intro), ""]
    for item in disclosure.items:
        status = "Required" if item.required else ("Optional" if item.enabled else "Not used")
        lines += [f"## {_md(item.title)} ({status})", ""]
        if item.summary:
            lines += [f"*{_md(item.summary)}*", ""]
        lines += [_md(item.description), ""]
    lines += [
        "## Purpose",
        "",
        _md(disclosure.purpose),
        "",
        "## Human decision",
        "",
        _md(disclosure.human_review_statement),
        "",
        "## Limitations",
        "",
        _md(disclosure.limitations_statement),
        "",
        f"Policy version {_md(disclosure.policy_version)}",
    ]
    return "\n".join(lines).strip() + "\n"


def consent_items(disclosure: Disclosure, snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The ``ConsentItems`` map the portal stores with the consent
    (``{item_key: enabled, ..., "retention_days": n}``)."""
    values: dict[str, Any] = {item.key: item.enabled for item in disclosure.items if item.key != "retention"}
    snap = parse_policy_snapshot(snapshot) if snapshot is not None else None
    values["retention_days"] = retention_days_for_consent(snap) if snap is not None else _max_days(disclosure)
    return values


def _max_days(disclosure: Disclosure) -> int:
    found = [int(n) for n in re.findall(r"(\d+) days?", disclosure.retention_summary)]
    return max(found) if found else 0


def required_item_keys(disclosure: Disclosure) -> list[str]:
    return [item.key for item in disclosure.items if item.required]


def missing_required_items(disclosure: Disclosure, accepted: Mapping[str, Any]) -> list[str]:
    """Required items the candidate did not acknowledge (``accepted[key]`` truthy)."""
    return [key for key in required_item_keys(disclosure) if key != "retention" and not accepted.get(key)]


def disclosure_payload(disclosure: Disclosure, snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """JSON body for ``GET /portal/interviews/{id}/disclosure``: every contract
    field plus ``text_markdown`` and ``consent_items`` (web ``Disclosure`` type)."""
    payload = asdict(disclosure)
    payload["text_markdown"] = render_disclosure_markdown(disclosure)
    payload["consent_items"] = consent_items(disclosure, snapshot)
    payload["required_items"] = required_item_keys(disclosure)
    return payload


__all__ = [
    "auto_deleted_items",
    "LIMITATIONS_CORE",
    "DefaultDisclosureBuilder",
    "build_disclosure",
    "compute_policy_hash",
    "consent_items",
    "disclosure_payload",
    "face_check_enabled",
    "missing_required_items",
    "render_disclosure_markdown",
    "required_item_keys",
    "retention_days_for_consent",
]
