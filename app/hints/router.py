from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse

import app.hints.presentation  # noqa: F401  (registers the template filters)
from app.core.rate_limit import HINTS_PER_IP, HINTS_PER_USER, allow
from app.core.security import CurrentUser, OptionalUser
from app.core.templates import templates
from app.games.dependencies import Now, Session, WatcherMembership
from app.games.page import build_game_page
from app.hints.service import HintForbidden, HintNotFound, delete_hint, post_hint

router = APIRouter(prefix="/hints", tags=["hints"])


@router.get("", response_class=HTMLResponse)
async def get_hints(
    request: Request, user: OptionalUser, session: Session, now: Now
) -> HTMLResponse:
    page = await build_game_page(session, user, now)
    return templates.TemplateResponse(request, "hints/board.html", page.as_context())


@router.post("", response_class=HTMLResponse)
async def create_hint(
    request: Request,
    membership: WatcherMembership,
    session: Session,
    now: Now,
    # Generous cap on the raw field; the 280-character rule is checked by the service
    # so the watcher gets a friendly message and keeps the text.
    body: Annotated[str, Form(max_length=2000)] = "",
) -> HTMLResponse:
    if not allow(request, membership.user.id, HINTS_PER_USER, HINTS_PER_IP):
        page = await build_game_page(session, membership.user, now)
        return templates.TemplateResponse(
            request,
            "hints/hint_response.html",
            {**page.as_context(), "result": None, "rate_limited": True, "draft": body},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    result = await post_hint(session, membership.game, membership.user.id, body)
    page = await build_game_page(session, membership.user, now)
    return templates.TemplateResponse(
        request, "hints/hint_response.html", {**page.as_context(), "result": result}
    )


@router.delete("/{hint_id}", response_class=HTMLResponse)
async def remove_hint(
    hint_id: int, request: Request, user: CurrentUser, session: Session, now: Now
) -> HTMLResponse:
    try:
        await delete_hint(session, hint_id, user, now)
    except HintNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dica não encontrada.") from None
    except HintForbidden:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Você não pode apagar esta dica.") from None
    page = await build_game_page(session, user, now)
    return templates.TemplateResponse(request, "hints/board.html", page.as_context())
