from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.auth.session import RefreshSessionMiddleware
from app.auth.supabase import SupabaseAuthClient
from app.auth.tokens import TokenVerifier
from app.core.config import get_settings
from app.games.router import router as games_router
from app.guesses.router import router as guesses_router
from app.hints.router import router as hints_router
from app.prizes.router import router as prizes_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10) as http:
        auth_client = SupabaseAuthClient(settings.supabase_url, settings.supabase_anon_key, http)
        app.state.auth_client = auth_client
        app.state.token_verifier = TokenVerifier(
            auth_client.fetch_jwks, issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1"
        )
        yield


app = FastAPI(title="Taqui o Contexto", lifespan=lifespan)
app.add_middleware(RefreshSessionMiddleware)
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
