"""Origin validation, ws_url construction, per-connection limits, policy parsing, providers."""

from __future__ import annotations

import pytest

from app.realtime import deps
from app.realtime.limits import ConnectionLimits, TokenBucket
from app.realtime.origin import build_ws_url, origin_allowed, ws_path
from app.realtime.policy import parse_policy_snapshot, session_policy

PUBLIC = "http://localhost:3200"


@pytest.mark.parametrize(
    ("origin", "host", "allowed"),
    [
        ("http://localhost:3200", "localhost:3200", True),  # public base url
        ("https://192.168.1.50:3443", "192.168.1.50:3443", True),  # same host (LAN over TLS edge)
        ("http://127.0.0.1:3200", "127.0.0.1:3200", True),
        ("https://interview.example.com", "interview.example.com", True),  # default port normalised
        ("https://interview.example.com:443", "interview.example.com", True),
        ("https://evil.example.com", "interview.example.com", False),
        ("https://192.168.1.50:3444", "192.168.1.50:3443", False),  # port mismatch
        ("http://localhost:3200.evil.com", "localhost:3200", False),
        ("null", "localhost:3200", False),
        ("file://localhost", "localhost", False),
        ("https://user@host.example", "host.example", False),
        ("https://allowed.example", "other:1", True),  # explicit CORS origin
    ],
)
def test_origin_rules(origin: str, host: str, allowed: bool) -> None:
    assert (
        origin_allowed(
            origin,
            host=host,
            public_base_url=PUBLIC,
            cors_allowed_origins=["https://allowed.example", "*"],
            allow_missing=False,
        )
        is allowed
    )


def test_missing_origin_depends_on_environment() -> None:
    kwargs = {"host": "localhost:3200", "public_base_url": PUBLIC, "cors_allowed_origins": []}
    assert origin_allowed(None, allow_missing=True, **kwargs)
    assert not origin_allowed(None, allow_missing=False, **kwargs)


def test_ws_url_uses_request_host_and_scheme() -> None:
    sid = "3f1c"
    assert (
        build_ws_url(scheme="https", host="10.0.0.5:3443", session_id=sid, public_base_url=PUBLIC)
        == f"wss://10.0.0.5:3443/ws/sessions/{sid}"
    )
    assert (
        build_ws_url(scheme="http", host="localhost:3200", session_id=sid, public_base_url=PUBLIC)
        == f"ws://localhost:3200/ws/sessions/{sid}"
    )
    assert (
        build_ws_url(scheme="http", host="[::1]:3200", session_id=sid, public_base_url=PUBLIC)
        == f"ws://[::1]:3200/ws/sessions/{sid}"
    )
    # Garbage Host headers fall back to PUBLIC_BASE_URL.
    assert (
        build_ws_url(
            scheme="http", host="evil.com/x?y", session_id=sid, public_base_url="https://app.example"
        )
        == f"wss://app.example/ws/sessions/{sid}"
    )
    assert ws_path(sid) == f"/ws/sessions/{sid}"


def test_token_bucket_refills() -> None:
    now = [0.0]
    bucket = TokenBucket(2, 3, clock=lambda: now[0])
    assert [bucket.allow() for _ in range(4)] == [True, True, True, False]
    now[0] += 0.5
    assert bucket.allow() and not bucket.allow()


def test_connection_limits_per_type_and_abuse() -> None:
    limits = ConnectionLimits()
    runs = [limits.allow_message("code.run") for _ in range(5)]
    assert runs[:3] == [True, True, True] and not any(runs[3:])
    assert not limits.abusive
    for _ in range(400):
        limits.allow_message("heartbeat")
    assert limits.abusive
    frames = ConnectionLimits()
    assert all(frames.allow_frame(3208) for _ in range(30))


def test_policy_snapshot_parsing_is_lenient() -> None:
    snapshot = parse_policy_snapshot(
        {
            "policy_version": "2026-10-01",
            "recording": {"record_answer_audio": False, "show_live_subtitles": False, "unknown_flag": 1},
            "integrity": {"camera_required": "not-a-bool"},
            "candidate_report_mode": "bogus",
            "typed_answers": True,
        }
    )
    assert snapshot.recording.record_answer_audio is False
    assert snapshot.recording.show_live_subtitles is False
    assert snapshot.integrity.camera_required is True  # invalid section -> defaults
    assert snapshot.candidate_report_mode.value == "never"  # invalid -> the platform default


def test_session_policy_requires_consent_for_recording_and_analysis() -> None:
    raw = {
        "recording": {"record_answer_audio": True, "record_video": True},
        "integrity": {"camera_frame_analysis": True},
    }
    no_consent = session_policy(raw, None)
    assert (
        not no_consent.record_answer_audio
        and not no_consent.video_recording_allowed
        and not no_consent.camera_frames_allowed
    )

    class _Consent:
        items = {"audio_recording": True, "video_recording": False, "camera_frames": True}

    policy = session_policy(raw, _Consent())  # type: ignore[arg-type]
    assert policy.record_answer_audio and not policy.video_recording_allowed and policy.camera_frames_allowed
    flags = policy.integrity_flags()
    assert flags["camera_frames"] is True and flags["record_video"] is False
    safe = policy.candidate_safe()
    assert "rules" not in safe["integrity"]


def test_provider_overrides_and_real_modules_resolve() -> None:
    sentinel = object()
    with deps.override_providers(turn_engine=sentinel):
        assert deps.get_turn_engine() is sentinel
    from app.services.integrity import get_integrity_service, set_integrity_service

    assert deps.get_integrity_service() is get_integrity_service()
    replacement = object()
    previous = set_integrity_service(replacement)
    try:
        assert deps.get_integrity_service() is replacement  # module factories are called on every access
    finally:
        set_integrity_service(previous)
    from app.services.integrity import build_disclosure

    assert deps.get_disclosure_builder() is build_disclosure


def test_null_integrity_fallback_when_module_missing(monkeypatch) -> None:
    monkeypatch.setitem(
        deps.SPECS, "integrity_service", deps._Spec("integrity_service", "app.services.nope", (), (), (), ())
    )
    deps._getters.pop("integrity_service", None)
    try:
        assert isinstance(deps.get_integrity_service(), deps.NullIntegrityService)
    finally:
        deps._getters.pop("integrity_service", None)


def test_missing_engine_raises_service_unavailable(monkeypatch) -> None:
    from app.core.errors import ServiceUnavailable

    monkeypatch.setitem(
        deps.SPECS, "turn_engine", deps._Spec("turn_engine", "app.services.does_not_exist", (), (), (), ())
    )
    deps._getters.pop("turn_engine", None)
    try:
        with pytest.raises(ServiceUnavailable):
            deps.get_turn_engine()
    finally:
        deps._getters.pop("turn_engine", None)


def test_uvicorn_log_records_never_carry_tickets_or_invite_tokens() -> None:
    import logging

    from app.realtime.router import install_log_redaction, redact_secrets

    install_log_redaction()
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        __file__,
        1,
        '%s - "WebSocket %s" [accepted]',
        (("127.0.0.1", 5000), "/ws/sessions/abc?ticket=yfavToIkLuZv6kYK_n9BS3RUbXRYYsea4rTNQXeE_BI"),
        None,
    )
    for flt in logging.getLogger("uvicorn.error").filters:
        flt.filter(record)
    assert "yfavToIk" not in record.getMessage() and "ticket=[redacted]" in record.getMessage()
    assert (
        redact_secrets("GET /api/v1/candidate-access/AbCdEfGhIjKlMnOpQrStUv/verify")
        == "GET /api/v1/candidate-access/[redacted]/verify"
    )


def test_bridge_sources_follow_the_integrity_rule_engine() -> None:
    """Audit: screen_share_not_full_screen is the browser's display-surface check
    (a browser source in the rule engine), not a screen-analysis model guess."""
    from app.domain.enums import IntegrityEventType as T
    from app.domain.enums import IntegritySource
    from app.realtime.bridges import event_source, objective_action_allowed
    from app.services.integrity.rules import default_source

    for event_type in T:
        assert event_source(event_type) == default_source(event_type), event_type
    assert event_source(T.SCREEN_SHARE_NOT_FULL_SCREEN) is IntegritySource.BROWSER
    assert event_source(T.SCREEN_CONTEXT_CHANGE) is IntegritySource.SCREEN
    assert objective_action_allowed(T.SCREEN_SHARE_NOT_FULL_SCREEN)
    assert not objective_action_allowed(T.SCREEN_CONTEXT_CHANGE)
    assert not objective_action_allowed(T.MULTIPLE_PERSONS_VISIBLE)


def test_only_real_executions_count_as_coding_results() -> None:
    from app.domain.enums import RunKind, RunStatus
    from app.realtime.bridges import is_execution_result
    from app.schemas.realtime import CodingRunResultMsg
    from app.services.coding.sandbox_client import SANDBOX_BUSY_MESSAGE

    def result(status: RunStatus, stderr: str = "") -> CodingRunResultMsg:
        return CodingRunResultMsg(
            coding_session_id="cs1", run_id="r1", kind=RunKind.SUBMIT, status=status, stderr=stderr
        )

    assert is_execution_result(result(RunStatus.PASSED))
    assert is_execution_result(result(RunStatus.FAILED))
    assert is_execution_result(result(RunStatus.COMPILE_ERROR, "SyntaxError"))
    assert is_execution_result(
        result(RunStatus.ERROR, "Traceback: ZeroDivisionError")
    )  # the candidate's error
    assert not is_execution_result(result(RunStatus.ERROR, SANDBOX_BUSY_MESSAGE))  # platform fault
    assert not is_execution_result(result(RunStatus.REJECTED, "A run is already in progress."))
    flagged = result(RunStatus.ERROR, "The code runner is temporarily unavailable.").model_copy(
        update={"platform_fault": True}
    )
    assert not is_execution_result(flagged)  # S20: the explicit flag, not the message text
