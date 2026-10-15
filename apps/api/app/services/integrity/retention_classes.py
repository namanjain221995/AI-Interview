"""What the candidate disclosure may promise about deletion, and what enforces it (G3, audit E1).

A data class the consent text calls "deleted automatically" must be a stored file: every file is registered
in ``media_assets`` with ``retention_until`` taken from the interview's policy, and ``retention.sweep``
deletes it after that date. Data kept as database rows - the transcript, the report content and the text and
profile read from a resume - has no enforcing sweep yet (C23 adds one); the disclosure says those stay until
the hiring team deletes them (staff erasure: ``app.services.candidates.erasure``).
"""

from __future__ import annotations

from app.domain.enums import MediaKind

# Disclosed class -> the stored-file kinds that carry it (each gets a retention deadline at store time).
AUTO_DELETED: dict[str, frozenset[MediaKind]] = {
    "resume_file": frozenset({MediaKind.RESUME}),
    "answer_audio": frozenset({MediaKind.ANSWER_AUDIO, MediaKind.SESSION_AUDIO}),
    "video": frozenset({MediaKind.VIDEO_SEGMENT, MediaKind.SCREEN_SEGMENT, MediaKind.SESSION_RECORDING}),
    "frames": frozenset({MediaKind.CAMERA_FRAME, MediaKind.SCREEN_FRAME}),
    "report_files": frozenset({MediaKind.REPORT_ARTIFACT}),
}

# Kept as rows until someone deletes them: never described with a deletion deadline until C23 enforces one.
KEPT_UNTIL_DELETED: frozenset[str] = frozenset({"transcript", "report_content", "resume_text"})

__all__ = ["AUTO_DELETED", "KEPT_UNTIL_DELETED"]
