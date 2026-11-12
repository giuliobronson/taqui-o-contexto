from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templates import templates

router = APIRouter(tags=["games"])


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "games/home.html")
