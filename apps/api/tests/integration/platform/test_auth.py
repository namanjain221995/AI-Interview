"""Authentication flows: login, cookies, lockout, refresh rotation and reuse
detection, logout, CSRF, password change, account/org deactivation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.core.security import decode_access_token
from app.db.models import AuditLog, AuthSession, Organization, User
from app.domain.enums import Role
from app.services.identity import sessions as session_service

pytestmark = pytest.mark.integration


def _cookie_headers(response: httpx.Response) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for raw in response.headers.get_list("set-cookie"):
        name = raw.split("=", 1)[0]
        cookies[name] = raw
    return cookies


async def _login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_login_sets_hardened_cookies_and_returns_principal(client, user_factory, db_session):
    user = await user_factory(Role.RECRUITER)
    response = await _login(client, user.email.upper(), user.password)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["principal"]["email"] == user.email
    assert body["principal"]["role"] == "RECRUITER"
    assert "jobs:write" in body["principal"]["permissions"] and "users:manage" not in body["principal"]["permissions"]
    assert body["principal"]["organization"]["id"] == str(user.organization_id)
    cookies = _cookie_headers(response)
    access, refresh, csrf = cookies["access_token"], cookies["refresh_token"], cookies["csrf_token"]
    assert "HttpOnly" in access and "Path=/api;" in access and "SameSite=lax" in access
    assert "HttpOnly" in refresh and "Path=/api/v1/auth" in refresh
    assert "HttpOnly" not in csrf and "Path=/;" in csrf
    assert body["csrf_token"] in csrf
    assert "access_token" not in response.text.replace('"access_token_expires_at"', "")
    claims = decode_access_token(client.cookies.get("access_token"))
    assert claims.role is Role.RECRUITER and str(claims.subject_id) == str(user.id)
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["id"] == str(user.id)
    row = await db_session.scalar(select(User).where(User.id == user.id))
    assert row.last_login_at is not None and row.failed_login_count == 0
    actions = (await db_session.scalars(select(AuditLog.action))).all()
    assert "auth.login" in actions


async def test_bearer_header_works_without_csrf(client, user_factory):
    user = await user_factory(Role.ADMIN)
    login = await _login(client, user.email, user.password)
    token = client.cookies.get("access_token")
    async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as bare:
        me = await bare.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        created = await bare.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": "bearer-made@example.test", "full_name": "Bearer Made", "role": "RECRUITER",
                  "password": "lantern-Orbit-5591"},
        )
        assert created.status_code == 201, created.text
        bad = await bare.get("/api/v1/auth/me", headers={"Authorization": "Basic abc"})
        assert bad.status_code == 401
    assert login.status_code == 200


async def test_wrong_password_and_unknown_email_look_identical(client, user_factory):
    user = await user_factory(Role.ADMIN)
    wrong = await _login(client, user.email, "definitely-not-it-123")
    unknown = await _login(client, "nobody-here@example.test", "definitely-not-it-123")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_lockout_after_repeated_failures(client, user_factory, db_session, settings):
    user = await user_factory(Role.RECRUITER)
    for _ in range(settings.login_max_attempts - 1):
        assert (await _login(client, user.email, "bad-password-0000")).status_code == 401
    locked = await _login(client, user.email, "bad-password-0000")
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "account_locked"
    assert int(locked.headers["retry-after"]) > 0
    # Correct password is refused while locked.
    assert (await _login(client, user.email, user.password)).status_code == 423
    row = await db_session.scalar(select(User).where(User.id == user.id))
    assert row.locked_until is not None
    actions = (await db_session.scalars(select(AuditLog.action).where(AuditLog.resource_id == str(user.id)))).all()
    assert "auth.account_locked" in actions and actions.count("auth.login_failed") >= settings.login_max_attempts
    # Lock expiry restores access.
    await db_session.execute(update(User).where(User.id == user.id).values(locked_until=datetime.now(UTC) - timedelta(seconds=1)))
    await db_session.commit()
    assert (await _login(client, user.email, user.password)).status_code == 200


async def test_unknown_email_gets_the_same_lockout(client, settings):
    # Exactly the same attempt pattern as a real account (see test_lockout_after_repeated_failures):
    # max_attempts - 1 plain failures, then the max_attempts-th failure locks.
    for _ in range(settings.login_max_attempts - 1):
        assert (await _login(client, "ghost@example.test", "whatever-123456")).status_code == 401
    locked = await _login(client, "ghost@example.test", "whatever-123456")
    assert locked.status_code == 423 and locked.json()["error"]["code"] == "account_locked"
    assert int(locked.headers["retry-after"]) > 0
    assert (await _login(client, "ghost@example.test", "whatever-123456")).status_code == 423


async def test_admin_can_unlock_user(client, auth_client, user_factory, settings):
    victim = await user_factory(Role.RECRUITER)
    for _ in range(settings.login_max_attempts):
        await _login(client, victim.email, "bad-password-0000")
    assert (await _login(client, victim.email, victim.password)).status_code == 423
    admin = await auth_client(Role.ADMIN, organization_id=victim.organization_id)
    unlocked = await admin.post(f"/api/v1/users/{victim.id}/unlock")
    assert unlocked.status_code == 200 and unlocked.json()["is_locked"] is False
    assert (await _login(client, victim.email, victim.password)).status_code == 200


async def test_login_rate_limit_per_ip(client):
    statuses = [(await _login(client, f"rl{i}@example.test", "x")).status_code for i in range(32)]
    assert statuses[-1] == 429
    limited = await _login(client, "rl@example.test", "x")
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1


async def test_inactive_user_and_disabled_org_cannot_log_in(client, user_factory, db_session, org_factory):
    inactive = await user_factory(Role.RECRUITER, is_active=False)
    response = await _login(client, inactive.email, inactive.password)
    assert response.status_code == 401 and response.json()["error"]["code"] == "account_disabled"
    org_id = await org_factory(is_active=False)
    member = await user_factory(Role.ADMIN, organization_id=org_id)
    assert (await _login(client, member.email, member.password)).status_code == 401


async def test_refresh_rotates_tokens_and_detects_reuse(client, user_factory, db_session):
    user = await user_factory(Role.INTERVIEWER)
    login = await _login(client, user.email, user.password)
    csrf = login.json()["csrf_token"]
    first_refresh = client.cookies.get("refresh_token")
    first_access = client.cookies.get("access_token")

    rotated = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert rotated.status_code == 200, rotated.text
    new_csrf = rotated.json()["csrf_token"]
    assert new_csrf != csrf
    assert client.cookies.get("refresh_token") != first_refresh
    assert client.cookies.get("access_token") != first_access
    rows = (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert len(rows) == 2 and len({r.family_id for r in rows}) == 1
    old_row = next(r for r in rows if r.used_at is not None)
    new_row = next(r for r in rows if r.used_at is None)
    assert new_row.rotated_from_id == old_row.id

    # Replay of the rotated token after the grace window revokes the family.
    await db_session.execute(
        update(AuthSession).where(AuthSession.id == old_row.id).values(used_at=datetime.now(UTC) - timedelta(minutes=5))
    )
    await db_session.commit()
    async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as attacker:
        attacker.cookies.set("refresh_token", first_refresh)
        attacker.cookies.set("csrf_token", csrf)
        replay = await attacker.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_token_reused"
    assert "refresh_token=" in replay.headers.get("set-cookie", "")  # cookies cleared
    db_session.expire_all()
    rows = (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert all(r.revoked_at is not None and r.revoked_reason == "reuse_detected" for r in rows)
    # The legitimate (now revoked) session is rejected immediately (cache invalidated).
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert (await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": new_csrf})).status_code == 401
    actions = (await db_session.scalars(select(AuditLog.action))).all()
    assert "auth.refresh_reuse_detected" in actions


async def test_concurrent_refresh_within_grace_window(client, user_factory, db_session):
    user = await user_factory(Role.RECRUITER)
    login = await _login(client, user.email, user.password)
    csrf = login.json()["csrf_token"]
    stale_refresh = client.cookies.get("refresh_token")
    assert (await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})).status_code == 200
    async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as other_tab:
        other_tab.cookies.set("refresh_token", stale_refresh)
        other_tab.cookies.set("csrf_token", csrf)
        late = await other_tab.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert late.status_code == 200, late.text
    assert "refresh_token=" not in late.headers.get("set-cookie", "")  # successor cookie untouched
    rows = (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert len(rows) == 2 and all(r.revoked_at is None for r in rows)
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_parallel_refreshes_never_fork_the_family(client, user_factory, db_session):
    user = await user_factory(Role.RECRUITER)
    login = await _login(client, user.email, user.password)
    csrf = login.json()["csrf_token"]
    responses = await asyncio.gather(
        *(client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf}) for _ in range(4))
    )
    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    rows = (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert len(rows) == 2  # exactly one rotation; the others were grace-window re-issues
    assert sum(1 for r in rows if r.used_at is None) == 1 and all(r.revoked_at is None for r in rows)


async def test_refresh_requires_csrf_and_valid_cookie(client, user_factory):
    user = await user_factory(Role.RECRUITER)
    await _login(client, user.email, user.password)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 403
    assert (await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "forged"})).status_code == 403
    client.cookies.delete("refresh_token")
    async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as anonymous:
        missing = await anonymous.post("/api/v1/auth/refresh")
    assert missing.status_code == 401 and missing.json()["error"]["code"] == "invalid_refresh_token"


async def test_logout_revokes_family_and_clears_cookies(client, user_factory, db_session):
    user = await user_factory(Role.ADMIN)
    login = await _login(client, user.email, user.password)
    csrf = login.json()["csrf_token"]
    access = client.cookies.get("access_token")
    assert (await client.post("/api/v1/auth/logout")).status_code == 403  # CSRF required
    out = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 204
    set_cookie = " ".join(out.headers.get_list("set-cookie"))
    assert "access_token=" in set_cookie and "Max-Age=0" in set_cookie
    rows = (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert rows and all(r.revoked_reason == "logout" for r in rows)
    async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as replay:
        me = await replay.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401 and me.json()["error"]["code"] == "session_revoked"
    async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as anonymous:
        assert (await anonymous.post("/api/v1/auth/logout")).status_code == 204  # idempotent


async def test_csrf_enforced_for_cookie_sessions(auth_client):
    admin = await auth_client(Role.ADMIN)
    payload = {"email": "csrf-check@example.test", "full_name": "Csrf Check", "role": "RECRUITER",
               "password": "quartz-Meadow-2048"}
    token = admin.headers.pop("X-CSRF-Token")
    missing = await admin.post("/api/v1/users", json=payload)
    assert missing.status_code == 403 and missing.json()["error"]["code"] == "csrf_failed"
    wrong = await admin.post("/api/v1/users", json=payload, headers={"X-CSRF-Token": token[:-2] + "xx"})
    assert wrong.status_code == 403
    assert (await admin.get("/api/v1/users")).status_code == 200  # safe methods need no token
    ok = await admin.post("/api/v1/users", json=payload, headers={"X-CSRF-Token": token})
    assert ok.status_code == 201


async def test_csrf_can_be_disabled_by_settings(auth_client, settings):
    admin = await auth_client(Role.ADMIN)
    admin.headers.pop("X-CSRF-Token")
    settings.csrf_enabled = False
    response = await admin.post(
        "/api/v1/users",
        json={"email": "nocsrf@example.test", "full_name": "No Csrf", "role": "INTERVIEWER", "password": "copper-Kettle-7731"},
    )
    assert response.status_code == 201


async def test_role_change_applies_immediately(auth_client, user_factory):
    target = await user_factory(Role.RECRUITER)
    target_client = await auth_client(user=target)
    assert (await target_client.get("/api/v1/auth/me")).json()["role"] == "RECRUITER"
    admin = await auth_client(Role.ADMIN, organization_id=target.organization_id)
    changed = await admin.patch(f"/api/v1/users/{target.id}", json={"role": "INTERVIEWER"})
    assert changed.status_code == 200
    me = await target_client.get("/api/v1/auth/me")
    assert me.json()["role"] == "INTERVIEWER"  # same access token, fresh role
    assert "jobs:write" not in me.json()["permissions"]


async def test_deactivation_ends_sessions(auth_client, user_factory):
    target = await user_factory(Role.INTERVIEWER)
    target_client = await auth_client(user=target)
    admin = await auth_client(Role.ADMIN, organization_id=target.organization_id)
    assert (await admin.post(f"/api/v1/users/{target.id}/deactivate")).status_code == 200
    response = await target_client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_org_deactivation_blocks_existing_sessions(auth_client, user_factory, db_session):
    member = await user_factory(Role.RECRUITER)
    member_client = await auth_client(user=member)
    root = await auth_client(Role.SUPER_ADMIN)
    response = await root.patch(f"/api/v1/organizations/{member.organization_id}", json={"is_active": False})
    assert response.status_code == 200
    # Cached auth state expires within 30 s; drop it to observe the effect now.
    from app.services.identity.auth_cache import invalidate_auth_cache

    ids = (await db_session.scalars(select(AuthSession.id).where(AuthSession.user_id == member.id))).all()
    await invalidate_auth_cache(ids)
    assert (await member_client.get("/api/v1/auth/me")).status_code == 401
    org = await db_session.get(Organization, member.organization_id)
    assert org.is_active is False


async def test_change_own_password(auth_client, client, db_session):
    staff = await auth_client(Role.RECRUITER)
    other_session = await auth_client(user=staff.user)
    bad = await staff.post("/api/v1/auth/password", json={"current_password": "nope-nope-nope", "new_password": "x"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_current_password"
    weak = await staff.post("/api/v1/auth/password", json={"current_password": staff.user.password, "new_password": "password1234"})
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "weak_password"
    ok = await staff.post(
        "/api/v1/auth/password", json={"current_password": staff.user.password, "new_password": "amber-Glacier-6612"}
    )
    assert ok.status_code == 204
    assert (await staff.get("/api/v1/auth/me")).status_code == 200  # current session kept
    assert (await other_session.get("/api/v1/auth/me")).status_code == 401  # others revoked
    assert (await _login(client, staff.user.email, "amber-Glacier-6612")).status_code == 200


async def test_relogin_supersedes_previous_family(client, user_factory, db_session):
    user = await user_factory(Role.RECRUITER)
    await _login(client, user.email, user.password)
    await _login(client, user.email, user.password)
    rows = (await db_session.scalars(select(AuthSession).where(AuthSession.user_id == user.id))).all()
    assert sorted(r.revoked_reason or "active" for r in rows) == ["active", "superseded"]


async def test_password_rehash_on_login(client, user_factory, db_session, settings):
    from argon2 import PasswordHasher

    user = await user_factory(Role.RECRUITER)
    legacy = PasswordHasher(time_cost=1, memory_cost=512, parallelism=1).hash(user.password)
    await db_session.execute(update(User).where(User.id == user.id).values(password_hash=legacy))
    await db_session.commit()
    assert (await _login(client, user.email, user.password)).status_code == 200
    db_session.expire_all()
    row = await db_session.get(User, user.id)
    assert row.password_hash != legacy and "m=1024" in row.password_hash


async def test_reuse_grace_constant_is_short():
    assert 0 < session_service.REFRESH_REUSE_GRACE_SECONDS <= 30
