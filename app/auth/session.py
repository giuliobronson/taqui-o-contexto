"""Session cookies and transparent refresh of expired access tokens."""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.auth.supabase import AuthError, AuthSession, SupabaseAuthClient
from app.auth.tokens import seconds_until_expiry
from app.core.config import get_settings

ACCESS_COOKIE = "sb_access"
REFRESH_COOKIE = "sb_refresh"
PKCE_COOKIE = "sb_pkce"

# Refresh slightly before expiry so a request never carries a token that dies mid-flight.
_REFRESH_MARGIN_SECONDS = 60
_REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def _cookie_options() -> dict:
    return {"httponly": True, "secure": get_settings().secure_cookies, "samesite": "lax"}


def set_session_cookies(response: Response, session: AuthSession) -> None:
    options = _cookie_options()
    response.set_cookie(ACCESS_COOKIE, session.access_token, max_age=session.expires_in, **options)
    response.set_cookie(
        REFRESH_COOKIE, session.refresh_token, max_age=_REFRESH_COOKIE_MAX_AGE, **options
    )


def clear_session_cookies(response: Response) -> None:
    options = _cookie_options()
    response.delete_cookie(ACCESS_COOKIE, **options)
    response.delete_cookie(REFRESH_COOKIE, **options)


def access_token_from(request: Request) -> str | None:
    """Access token for this request (a freshly refreshed one takes precedence)."""
    return getattr(request.state, "access_token", None) or request.cookies.get(ACCESS_COOKIE)


class RefreshSessionMiddleware(BaseHTTPMiddleware):
    """Swaps an expired or missing access token for a new one using the refresh token."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        refresh_token = request.cookies.get(REFRESH_COOKIE)
        access_token = request.cookies.get(ACCESS_COOKIE)
        needs_refresh = refresh_token is not None and (
            access_token is None or seconds_until_expiry(access_token) < _REFRESH_MARGIN_SECONDS
        )
        if not needs_refresh:
            return await call_next(request)

        client: SupabaseAuthClient = request.app.state.auth_client
        try:
            new_session = await client.refresh(refresh_token)
        except AuthError:
            request.state.access_token = None
            response = await call_next(request)
            clear_session_cookies(response)
            return response

        request.state.access_token = new_session.access_token
        response = await call_next(request)
        set_session_cookies(response, new_session)
        return response
