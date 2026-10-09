"""Interview recordings: segment upload, finalize (join + remux), staff view.

* :mod:`.segments` accepts the browser's MediaRecorder timeslices (portal).
* :mod:`.parts` groups ``media_assets`` rows into recording parts and derives
  their status and timing (no extra tables).
* :mod:`.finalize` joins a part's segments and remuxes them with ffmpeg into
  one seekable ``session_recording`` file (job ``recording.finalize``).
* :mod:`.sweep` queues finalize for parts that are over (job ``recording.sweep``).
* :mod:`.views` builds the staff Recording tab payload.

Retention and data-subject erasure need nothing special: segments and
finalized files are ordinary ``media_assets`` rows with ``retention_until``,
``session_id`` and ``candidate_id`` (see ``app.services.integrity.retention``
and ``app.services.candidates.erasure``).
"""

from app.services.recordings.constants import FINALIZE_JOB, SWEEP_JOB

__all__ = ["FINALIZE_JOB", "SWEEP_JOB"]
