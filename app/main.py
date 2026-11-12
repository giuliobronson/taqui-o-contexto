from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.games.router import router as games_router
from app.guesses.router import router as guesses_router
from app.hints.router import router as hints_router
from app.prizes.router import router as prizes_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Taqui o Contexto")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

for router in (
    auth_router,
    games_router,
    guesses_router,
    hints_router,
    prizes_router,
    admin_router,
):
    app.include_router(router)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}
