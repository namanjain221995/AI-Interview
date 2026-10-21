"""Staff and candidate sessions in one browser (G2): separate cookie sets, scoped sign-out, migration window."""

from __future__ import annotations

import httpx
import pytest

from app.domain.enums import Role

from .conftest import create_interview

pytestmark = pytest.mark.integration

BASE = "https://192.168.1.50:3443"


def _browser(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.1.2.3", 50000)), base_url=BASE
    )


def _set_cookie_names(response: httpx.Response) -> dict[str, str]:
    return {raw.split("=", 1)[0]: raw for raw in response.headers.get_list("set-cookie")}


async def test_opening_a_candidate_link_keeps_the_recruiter_signed_in(
    app, org_factory, user_factory, portal_fakes
) -> None:
    org = await org_factory()
    staff = await user_factory(Role.RECRUITER, organization_id=org)
    invite = await create_interview(org, consent_items={"microphone": True}, session_status="ready")
    browser = _browser(app)
    login = await browser.post("/api/v1/auth/login", json={"email": staff.email, "password": staff.password})
    assert login.status_code == 200
    staff_csrf = login.json()["csrf_token"]
    assert (await browser.get("/api/v1/jobs")).status_code == 200

    verified = await browser.post(
        f"/api/v1/candidate-access/{invite['invite_token']}/verify", json={"email": invite["candidate_email"]}
    )
    assert verified.status_code == 200, verified.text
    cookies = _set_cookie_names(verified)
    assert {"cand_access_token", "cand_refresh_token", "cand_csrf_token"} <= set(cookies)
    assert not {"access_token", "refresh_token", "csrf_token"} & set(cookies)  # the staff set is untouched
    candidate_csrf = verified.json()["csrf_token"]

    assert (await browser.get("/api/v1/jobs")).status_code == 200  # still the recruiter
    assert (await browser.get("/api/v1/portal/me")).status_code == 200  # and the candidate on candidate pages
    staff_me = (await browser.get("/api/v1/auth/me")).json()
    assert staff_me["role"] == "RECRUITER"

    # The candidate signs out (shared endpoint, candidate audience): only the candidate set goes.
    out = await browser.post(
        "/api/v1/auth/logout", headers={"X-Session-Audience": "candidate", "X-CSRF-Token": candidate_csrf}
    )
    assert out.status_code == 204
    cleared = _set_cookie_names(out)
    assert {"cand_access_token", "cand_refresh_token", "cand_csrf_token"} <= set(cleared)
    assert not {"access_token", "refresh_token", "csrf_token"} & set(cleared)
    browser.cookies.delete("cand_access_token")
    assert (await browser.get("/api/v1/portal/me")).status_code == 401
    assert (await browser.get("/api/v1/jobs")).status_code == 200
    # The staff CSRF still matches the staff session.
    assert (
        await browser.post("/api/v1/auth/logout", headers={"X-CSRF-Token": staff_csrf})
    ).status_code == 204
    await browser.aclose()


async def test_a_candidate_session_from_before_the_split_keeps_working_and_migrates(
    app, org_factory, portal_fakes
) -> None:
    org = await org_factory()
    invite = await create_interview(org, consent_items={"microphone": True}, session_status="ready")
    fresh = _browser(app)
    verified = await fresh.post(
        f"/api/v1/candidate-access/{invite['invite_token']}/verify", json={"email": invite["candidate_email"]}
    )
    assert verified.status_code == 200
    # What a pre-G2 browser holds: the candidate's tokens under the staff cookie names.
    legacy = _browser(app)
    for new, old in (
        ("cand_access_token", "access_token"),
        ("cand_refresh_token", "refresh_token"),
        ("cand_csrf_token", "csrf_token"),
    ):
        value = fresh.cookies.get(new)
        assert value
        legacy.cookies.set(old, value)
    await fresh.aclose()
    assert (await legacy.get("/api/v1/portal/me")).status_code == 200
    refreshed = await legacy.post(
        "/api/v1/auth/refresh",
        headers={"X-Session-Audience": "candidate", "X-CSRF-Token": legacy.cookies.get("csrf_token") or ""},
    )
    assert refreshed.status_code == 200, refreshed.text
    cookies = refreshed.headers.get_list("set-cookie")
    assert any(c.startswith("cand_access_token=") and "Max-Age=0" not in c for c in cookies)
    assert any(
        c.startswith("access_token=") and ("Max-Age=0" in c or "expires=Thu, 01 Jan 1970" in c)
        for c in cookies
    )
    await legacy.aclose()


_PRE_SPLIT_NAMES = {
    "cand_access_token": "access_token",
    "cand_refresh_token": "refresh_token",
    "cand_csrf_token": "csrf_token",
}


async def _pre_split_browser(app, invite) -> httpx.AsyncClient:
    """A browser whose candidate session was issued before G2: the same cookies under the staff names."""
    fresh = _browser(app)
    verified = await fresh.post(
        f"/api/v1/candidate-access/{invite['invite_token']}/verify", json={"email": invite["candidate_email"]}
    )
    assert verified.status_code == 200, verified.text
    legacy = _browser(app)
    for cookie in fresh.cookies.jar:
        if cookie.name in _PRE_SPLIT_NAMES and cookie.value:
            legacy.cookies.set(
                _PRE_SPLIT_NAMES[cookie.name], cookie.value, domain=cookie.domain, path=cookie.path
            )
    await fresh.aclose()
    return legacy


async def test_a_tab_running_the_pre_split_bundle_keeps_its_session_across_refreshes(
    app, org_factory, portal_fakes
) -> None:
    org = await org_factory()
    invite = await create_interview(org, consent_items={"microphone": True}, session_status="ready")
    legacy = await _pre_split_browser(app, invite)
    for _ in range(2):  # the second refresh presents the rotated token: never a false "reuse"
        # The pre-G2 bundle sends no audience header and reads its CSRF token from the staff name.
        refreshed = await legacy.post(
            "/api/v1/auth/refresh", headers={"X-CSRF-Token": legacy.cookies.get("csrf_token") or ""}
        )
        assert refreshed.status_code == 200, refreshed.text
        names = set(_set_cookie_names(refreshed))
        assert {"access_token", "refresh_token", "csrf_token"} <= names
        assert not {"cand_access_token", "cand_refresh_token", "cand_csrf_token"} & names
    assert (await legacy.get("/api/v1/portal/me")).status_code == 200
    # Unsafe candidate calls (ws-ticket, uploads, end) still pass CSRF with the token that bundle sends.
    unknown = "00000000-0000-4000-8000-000000000000"
    ended = await legacy.post(
        f"/api/v1/portal/sessions/{unknown}/end",
        json={"reason": "candidate_ended"},
        headers={"X-CSRF-Token": legacy.cookies.get("csrf_token") or ""},
    )
    assert ended.status_code == 404, ended.text
    await legacy.aclose()


async def test_a_candidate_page_never_refreshes_or_ends_the_staff_session(
    app, org_factory, user_factory
) -> None:
    org = await org_factory()
    staff = await user_factory(Role.RECRUITER, organization_id=org)
    browser = _browser(app)
    login = await browser.post("/api/v1/auth/login", json={"email": staff.email, "password": staff.password})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    # A candidate page without a candidate session: the SPA falls back to the staff CSRF cookie.
    candidate_page = {"X-Session-Audience": "candidate", "X-CSRF-Token": csrf}
    refreshed = await browser.post("/api/v1/auth/refresh", headers=candidate_page)
    assert refreshed.status_code == 401, refreshed.text
    assert not {"access_token", "refresh_token", "csrf_token"} & set(_set_cookie_names(refreshed))
    out = await browser.post("/api/v1/auth/logout", headers=candidate_page)
    assert out.status_code == 204
    assert not {"access_token", "refresh_token", "csrf_token"} & set(_set_cookie_names(out))
    assert (await browser.get("/api/v1/jobs")).status_code == 200  # still the recruiter
    staff_refresh = await browser.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert staff_refresh.status_code == 200, staff_refresh.text  # the family was never revoked
    await browser.aclose()


async def test_cookie_name_prefix_keeps_two_copies_on_one_host_apart(
    app, settings, org_factory, user_factory
) -> None:
    """COOKIE_NAME_PREFIX (dev on :3201 next to production on :3200): every cookie carries the prefix, and the
    unprefixed cookies of the other copy are never read."""
    settings.cookie_name_prefix = "dev_"
    org = await org_factory()
    staff = await user_factory(Role.RECRUITER, organization_id=org)
    browser = _browser(app)
    login = await browser.post("/api/v1/auth/login", json={"email": staff.email, "password": staff.password})
    assert login.status_code == 200, login.text
    names = set(_set_cookie_names(login))
    assert {"dev_access_token", "dev_refresh_token", "dev_csrf_token"} <= names
    assert not {"access_token", "refresh_token", "csrf_token"} & names
    csrf = login.json()["csrf_token"]
    assert (await browser.get("/api/v1/jobs")).status_code == 200

    # The other copy's session cookie (no prefix) is not a session here.
    other = _browser(app)
    other.cookies.set("access_token", browser.cookies.get("dev_access_token") or "")
    assert (await other.get("/api/v1/jobs")).status_code == 401
    await other.aclose()

    refreshed = await browser.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert refreshed.status_code == 200, refreshed.text
    assert {"dev_access_token", "dev_csrf_token"} <= set(_set_cookie_names(refreshed))
    out = await browser.post("/api/v1/auth/logout", headers={"X-CSRF-Token": refreshed.json()["csrf_token"]})
    assert out.status_code == 204
    assert {"dev_access_token", "dev_refresh_token", "dev_csrf_token"} <= set(_set_cookie_names(out))
    await browser.aclose()
