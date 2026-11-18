"""Request authentication: who is calling, and are they allowed to."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.auth.service import ensure_profile
from app.auth.session import access_token_from
from app.auth.tokens import InvalidToken, TokenVerifier
from app.core.db import get_session


def get_token_verifier(request: Request) -> TokenVerifier:
    return request.app.state.token_verifier


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> Profile | None:
    """The logged-in user's profile, or None for visitors."""
    token = access_token_from(request)
    if not token:
        return None
    try:
        claims = await verifier.verify(token)
    except InvalidToken:
        return None
    return await ensure_profile(session, claims)


OptionalUser = Annotated[Profile | None, Depends(current_user)]


async def require_user(user: OptionalUser) -> Profile:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Faça login para continuar.")
    return user


CurrentUser = Annotated[Profile, Depends(require_user)]


async def require_admin(user: CurrentUser) -> Profile:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso restrito a administradores.")
    return user


AdminUser = Annotated[Profile, Depends(require_admin)]
