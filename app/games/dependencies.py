from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.core.clock import utcnow
from app.core.db import get_session
from app.core.security import CurrentUser
from app.games.models import Game, GameMember, MemberRole
from app.games.service import join_active_game

Now = Annotated[datetime, Depends(utcnow)]
Session = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class Membership:
    user: Profile
    game: Game
    member: GameMember


def require_game_role(*roles: MemberRole) -> Callable[..., Awaitable[Membership]]:
    """Dependency: the user must hold one of `roles` in the active game.

    Responses: 401 when not logged in, 409 when there is no active game,
    403 when the user is an admin or holds a different role.
    """

    async def dependency(user: CurrentUser, session: Session, now: Now) -> Membership:
        if user.is_admin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Administradores não participam do jogo."
            )
        member = await join_active_game(session, user, now)
        if member is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Não há jogo ativo no momento.")
        if member.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Seu papel neste jogo não permite isso.")
        game = await session.get(Game, member.game_id)
        assert game is not None
        return Membership(user=user, game=game, member=member)

    return dependency


PlayerMembership = Annotated[Membership, Depends(require_game_role(MemberRole.PLAYER))]
WatcherMembership = Annotated[Membership, Depends(require_game_role(MemberRole.WATCHER))]
