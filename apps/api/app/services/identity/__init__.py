"""Identity: staff accounts, organizations, auth sessions and cookies.

Entry points for other modules:

* :func:`app.services.identity.sessions.start_candidate_session` +
  :func:`app.services.identity.cookies.set_auth_cookies` - sign a verified
  candidate in (candidate-access flow).
* :func:`app.services.identity.organizations.get_organization_settings` -
  effective ``OrganizationSettings`` (policy defaults for new interviews).
* :func:`app.services.identity.sessions.revoke_family` /
  ``revoke_user_sessions`` - end sessions.
"""

from app.services.identity.cookies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.services.identity.organizations import get_organization_settings, load_organization_settings
from app.services.identity.sessions import (
    IssuedTokens,
    revoke_family,
    revoke_user_sessions,
    rotate_refresh_token,
    start_candidate_session,
    start_user_session,
)

__all__ = [
    "ACCESS_COOKIE",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "REFRESH_COOKIE",
    "IssuedTokens",
    "clear_auth_cookies",
    "get_organization_settings",
    "load_organization_settings",
    "revoke_family",
    "revoke_user_sessions",
    "rotate_refresh_token",
    "set_auth_cookies",
    "start_candidate_session",
    "start_user_session",
]
