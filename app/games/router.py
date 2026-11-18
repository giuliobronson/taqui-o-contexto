from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.core.security import OptionalUser
from app.core.templates import templates
from app.games.dependencies import Now, Session
from app.games.models import Game, GameStatus
from app.games.page import build_game_page

router = APIRouter(tags=["games"])


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request, user: OptionalUser, session: Session, now: Now, celebrate: bool = False
) -> HTMLResponse:
    page = await build_game_page(session, user, now)
    # celebrate=1 comes from the winning guess: open the victory modal right away.
    context = {**page.as_context(), "celebrate": celebrate and page.is_winner}
    return templates.TemplateResponse(request, "games/home.html", context)


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def finished_game(
    game_id: int, request: Request, user: OptionalUser, session: Session, now: Now
) -> HTMLResponse:
    game = await session.get(Game, game_id)
    # Only finished games are public: anything else could leak the secret word.
    if game is None or game.status != GameStatus.FINISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jogo não encontrado.")
    page = await build_game_page(session, user, now, game)
    return templates.TemplateResponse(request, "games/home.html", page.as_context())
