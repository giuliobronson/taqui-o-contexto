from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import ensure_profile
from app.auth.session import (
    PKCE_COOKIE,
    access_token_from,
    clear_session_cookies,
    set_session_cookies,
)
from app.auth.supabase import AuthError, SupabaseAuthClient, new_pkce_pair
from app.auth.tokens import InvalidToken, TokenVerifier
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import get_token_verifier
from app.games.service import join_active_game

router = APIRouter(prefix="/auth", tags=["auth"])

_PKCE_MAX_AGE = 600


def get_auth_client(request: Request) -> SupabaseAuthClient:
    return request.app.state.auth_client


AuthClient = Annotated[SupabaseAuthClient, Depends(get_auth_client)]


def _callback_url() -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/auth/callback"


@router.get("/login")
async def login(client: AuthClient) -> RedirectResponse:
    verifier, challenge = new_pkce_pair()
    response = RedirectResponse(
        client.authorize_url(
            provider="google", redirect_to=_callback_url(), code_challenge=challenge
        ),
        status_code=302,
    )
    response.set_cookie(
        PKCE_COOKIE,
        verifier,
        max_age=_PKCE_MAX_AGE,
        path="/auth",
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite="lax",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    client: AuthClient,
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
    session: Annotated[AsyncSession, Depends(get_session)],
    now: Annotated[datetime, Depends(utcnow)],
    code: str | None = None,
) -> RedirectResponse:
    code_verifier = request.cookies.get(PKCE_COOKIE)
    failure = RedirectResponse("/?login=erro", status_code=302)
    failure.delete_cookie(PKCE_COOKIE, path="/auth")
    if not code or not code_verifier:
        return failure

    try:
        auth_session = await client.exchange_code(code, code_verifier)
        claims = await verifier.verify(auth_session.access_token)
    except (AuthError, InvalidToken):
        return failure

    profile = await ensure_profile(session, claims)
    await join_active_game(session, profile, now)

    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(PKCE_COOKIE, path="/auth")
    set_session_cookies(response, auth_session)
    return response


@router.post("/logout")
async def logout(request: Request, client: AuthClient) -> RedirectResponse:
    token = access_token_from(request)
    if token:
        await client.sign_out(token)
    response = RedirectResponse("/", status_code=303)
    clear_session_cookies(response)
    return response
