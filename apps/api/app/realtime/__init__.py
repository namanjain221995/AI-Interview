"""Realtime interview runtime (``/ws/sessions/{session_id}``).

See ``docs/design/REALTIME_DESIGN.md``. Layout:

* ``router``      WebSocket endpoint (ticket auth, Origin check, receive loop)
* ``runtime``     ``SessionRuntime``: one per live session, survives reconnects
* ``registry``    process-wide runtime registry + Redis session lock
* ``audio``       PCM ring buffer, Silero/energy VAD, segmentation
* ``stt``         ordered segment finals + throttled partials
* ``endpointing`` adaptive end-of-turn
* ``chunker`` / ``speech`` / ``acknowledgements``  interviewer speech out
* ``turns``       floor ownership FSM, spoken-portion + backchannel helpers
* ``bridges``     coding / integrity / session bus bridges
* ``outbox``      sequenced server messages + replay
* ``deps``        provider functions for engine / services (injectable)
"""
