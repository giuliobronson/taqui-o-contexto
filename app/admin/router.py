import uuid
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import app.guesses.presentation  # noqa: F401  (registers the template filters)
from app.admin.service import (
    AdminChangeError,
    GameForm,
    RoleChangeError,
    create_game,
    delete_game,
    editable_fields,
    finish,
    game_members,
    game_summaries,
    game_summary,
    list_users,
    make_watcher,
    set_admin,
    unlock,
    update_game,
    validate,
)
from app.core.config import get_settings
from app.core.security import AdminUser
from app.core.templates import templates
from app.games.dependencies import Now, Session
from app.games.models import Game
from app.games.service import GameStateError, current_game

router = APIRouter(prefix="/admin", tags=["admin"])

NOTICES = {
    "created": "Jogo criado e ranking calculado.",
    "saved": "Alterações salvas.",
    "unlocked": "Jogo liberado: palpites e dicas estão abertos.",
    "finished": "Jogo encerrado.",
    "deleted": "Jogo excluído.",
    "watcher": "Agora é watcher neste jogo.",
    "promoted": "Usuário promovido a admin.",
    "demoted": "Papel de admin removido.",
}

FormField = Annotated[str, Form(max_length=300)]


def _redirect(request: Request, url: str) -> Response:
    """Plain 303 for form posts; HX-Redirect for HTMX requests."""
    if request.headers.get("HX-Request"):
        return Response(status_code=status.HTTP_200_OK, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


async def _game_or_404(session: Session, game_id: int) -> Game:
    game = await session.get(Game, game_id)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jogo não encontrado.")
    return game


def _form(
    secret_word: str, starts_at: str, ends_at: str, prize_code: str, prize_description: str
) -> GameForm:
    return GameForm(secret_word, starts_at, ends_at, prize_code, prize_description)


@router.get("", response_class=HTMLResponse)
async def admin_home(
    request: Request, user: AdminUser, session: Session, now: Now, notice: str | None = None
) -> HTMLResponse:
    await current_game(session, now)  # apply pending transitions so states are current
    return templates.TemplateResponse(
        request,
        "admin/games.html",
        {"user": user, "games": await game_summaries(session), "notice": NOTICES.get(notice)},
    )


@router.get("/games/new", response_class=HTMLResponse)
async def new_game_form(request: Request, user: AdminUser) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/game_form.html",
        {"user": user, "form": GameForm(), "summary": None, "editable": editable_fields(None)},
    )


@router.post("/games/new", response_class=HTMLResponse)
async def create_game_action(
    request: Request,
    user: AdminUser,
    session: Session,
    now: Now,
    secret_word: FormField = "",
    starts_at: FormField = "",
    ends_at: FormField = "",
    prize_code: FormField = "",
    prize_description: FormField = "",
) -> Response:
    form = _form(secret_word, starts_at, ends_at, prize_code, prize_description)
    version = get_settings().vocabulary_version
    values = await validate(session, form, now, version)
    if values is None:
        return templates.TemplateResponse(
            request,
            "admin/game_form.html",
            {"user": user, "form": form, "summary": None, "editable": editable_fields(None)},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    game = await create_game(session, values, version, user.id)
    return _redirect(request, f"/admin/games/{game.id}?notice=created")


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def game_detail(
    game_id: int,
    request: Request,
    user: AdminUser,
    session: Session,
    now: Now,
    notice: str | None = None,
) -> HTMLResponse:
    await current_game(session, now)
    await _game_or_404(session, game_id)
    summary = await game_summary(session, game_id)
    assert summary is not None
    return templates.TemplateResponse(
        request,
        "admin/game_form.html",
        {
            "user": user,
            "summary": summary,
            "form": GameForm.from_game(summary.game),
            "editable": editable_fields(summary.game, summary.has_prize),
            "notice": NOTICES.get(notice),
        },
    )


@router.post("/games/{game_id}", response_class=HTMLResponse)
async def update_game_action(
    game_id: int,
    request: Request,
    user: AdminUser,
    session: Session,
    now: Now,
    secret_word: FormField = "",
    starts_at: FormField = "",
    ends_at: FormField = "",
    prize_code: FormField = "",
    prize_description: FormField = "",
) -> Response:
    await current_game(session, now)
    game = await _game_or_404(session, game_id)
    summary = await game_summary(session, game_id)
    assert summary is not None
    editable = editable_fields(game, summary.has_prize)
    if not editable:
        raise HTTPException(status.HTTP_409_CONFLICT, "Este jogo não pode mais ser editado.")

    form = _form(secret_word, starts_at, ends_at, prize_code, prize_description)
    values = await validate(session, form, now, game.model_version, game, summary.has_prize)
    if values is None:
        return templates.TemplateResponse(
            request,
            "admin/game_form.html",
            {"user": user, "summary": summary, "form": form, "editable": editable},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    await update_game(session, game, values)
    return _redirect(request, f"/admin/games/{game.id}?notice=saved")


@router.post("/games/{game_id}/unlock")
async def unlock_action(
    game_id: int, request: Request, user: AdminUser, session: Session, now: Now
) -> Response:
    await current_game(session, now)
    game = await _game_or_404(session, game_id)
    try:
        await unlock(session, game, user.id, now)
    except GameStateError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Só um jogo bloqueado pode ser liberado."
        ) from None
    return _redirect(request, f"/admin/games/{game.id}?notice=unlocked")


@router.post("/games/{game_id}/finish")
async def finish_action(
    game_id: int, request: Request, user: AdminUser, session: Session, now: Now
) -> Response:
    await current_game(session, now)
    game = await _game_or_404(session, game_id)
    try:
        await finish(session, game, now)
    except GameStateError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Só um jogo ativo pode ser encerrado."
        ) from None
    return _redirect(request, f"/admin/games/{game.id}?notice=finished")


@router.delete("/games/{game_id}")
async def delete_action(
    game_id: int, request: Request, user: AdminUser, session: Session, now: Now
) -> Response:
    await current_game(session, now)
    game = await _game_or_404(session, game_id)
    try:
        await delete_game(session, game)
    except GameStateError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Só um jogo agendado pode ser excluído."
        ) from None
    return _redirect(request, "/admin?notice=deleted")


@router.get("/games/{game_id}/members", response_class=HTMLResponse)
async def members_page(
    game_id: int,
    request: Request,
    user: AdminUser,
    session: Session,
    now: Now,
    notice: str | None = None,
) -> HTMLResponse:
    await current_game(session, now)
    game = await _game_or_404(session, game_id)
    return templates.TemplateResponse(
        request,
        "admin/members.html",
        {
            "user": user,
            "game": game,
            "members": await game_members(session, game_id),
            "notice": NOTICES.get(notice),
        },
    )


@router.post("/games/{game_id}/members/{user_id}/role")
async def change_role(
    game_id: int,
    user_id: uuid.UUID,
    request: Request,
    user: AdminUser,
    session: Session,
    now: Now,
    role: Annotated[str, Form(max_length=20)] = "watcher",
) -> Response:
    await current_game(session, now)
    game = await _game_or_404(session, game_id)
    if role != "watcher":
        raise HTTPException(status.HTTP_409_CONFLICT, "Um watcher não volta a ser player.")
    try:
        await make_watcher(session, game, user_id)
    except RoleChangeError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    return _redirect(request, f"/admin/games/{game.id}/members?notice=watcher")


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    user: AdminUser,
    session: Session,
    now: Now,
    q: Annotated[str, Query(max_length=100)] = "",
    notice: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "user": user,
            "users": await list_users(session, now, q),
            "q": q,
            "notice": NOTICES.get(notice),
        },
    )


@router.post("/users/{user_id}")
async def change_admin(
    user_id: uuid.UUID,
    request: Request,
    user: AdminUser,
    session: Session,
    now: Now,
    admin: Annotated[bool, Form()],
) -> Response:
    try:
        await set_admin(session, user, user_id, admin, now)
    except AdminChangeError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    return _redirect(request, f"/admin/users?notice={'promoted' if admin else 'demoted'}")
