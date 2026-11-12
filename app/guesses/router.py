from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import app.guesses.presentation  # noqa: F401  (registers the template filters)
from app.core.security import CurrentUser, OptionalUser
from app.core.templates import templates
from app.games.dependencies import Now, Session
from app.games.page import FormState, build_game_page
from app.guesses.service import submit_guess

router = APIRouter(tags=["guesses"])


@router.post("/guesses", response_class=HTMLResponse)
async def post_guess(
    request: Request,
    user: CurrentUser,
    session: Session,
    now: Now,
    word: Annotated[str, Form(max_length=60)] = "",
) -> HTMLResponse:
    page = await build_game_page(session, user, now)
    result = None
    # Role, game state and daily limit are all checked server-side here.
    if page.form_state == FormState.READY and page.game is not None:
        result = await submit_guess(session, page.game, user.id, word, now)
        page = await build_game_page(session, user, now)
    return templates.TemplateResponse(
        request, "guesses/guess_response.html", {**page.as_context(), "result": result}
    )


@router.get("/ranking", response_class=HTMLResponse)
async def get_ranking(
    request: Request,
    user: OptionalUser,
    session: Session,
    now: Now,
    status: str | None = None,
) -> HTMLResponse:
    page = await build_game_page(session, user, now)
    response = templates.TemplateResponse(request, "guesses/ranking.html", page.as_context())
    # The page was rendered for another state (game unlocked, finished, new game):
    # ask HTMX to reload it so the form and banners match.
    if status is not None and status != (page.game.status if page.game else "none"):
        response.headers["HX-Refresh"] = "true"
    return response
