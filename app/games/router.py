from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.security import OptionalUser
from app.core.templates import templates
from app.games.dependencies import Now, Session
from app.games.service import join_active_game

router = APIRouter(tags=["games"])


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, user: OptionalUser, session: Session, now: Now) -> HTMLResponse:
    if user is not None:
        # Covers users who were already logged in when a new game started.
        await join_active_game(session, user, now)
    return templates.TemplateResponse(request, "games/home.html", {"user": user})
