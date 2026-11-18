"""Static pages (privacy policy), required to publish the Google OAuth app."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.security import OptionalUser
from app.core.templates import templates

router = APIRouter(tags=["pages"])


@router.get("/privacidade", response_class=HTMLResponse)
async def privacy(request: Request, user: OptionalUser) -> HTMLResponse:
    return templates.TemplateResponse(request, "core/privacy.html", {"user": user})
