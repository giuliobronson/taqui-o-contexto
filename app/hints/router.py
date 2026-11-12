from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import app.hints.presentation  # noqa: F401  (registers the template filters)
from app.core.security import OptionalUser
from app.core.templates import templates
from app.games.dependencies import Now, Session, WatcherMembership
from app.games.page import build_game_page
from app.hints.service import post_hint

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
    result = await post_hint(session, membership.game, membership.user.id, body)
    page = await build_game_page(session, membership.user, now)
    return templates.TemplateResponse(
        request, "hints/hint_response.html", {**page.as_context(), "result": result}
    )
