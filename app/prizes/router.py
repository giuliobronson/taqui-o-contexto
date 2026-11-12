from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

import app.guesses.presentation  # noqa: F401  (registers the template filters)
from app.core.security import CurrentUser
from app.core.templates import templates
from app.games.dependencies import Session
from app.prizes.service import prizes_of

router = APIRouter(prefix="/prizes", tags=["prizes"])


@router.get("/me", response_class=HTMLResponse)
async def my_prizes(request: Request, user: CurrentUser, session: Session) -> HTMLResponse:
    """The user's coupons. Only the winner has one; everyone else sees an empty state."""
    prizes = await prizes_of(session, user.id)
    return templates.TemplateResponse(
        request, "prizes/my_prizes.html", {"user": user, "prizes": prizes}
    )
