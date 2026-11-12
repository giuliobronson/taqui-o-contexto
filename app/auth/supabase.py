"""Thin client for the Supabase Auth (GoTrue) endpoints the backend needs."""

import base64
import contextlib
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


class AuthError(Exception):
    """Supabase rejected the request (bad code, expired refresh token, ...)."""


@dataclass(frozen=True)
class AuthSession:
    access_token: str
    refresh_token: str
    expires_in: int


def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the OAuth PKCE flow."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class SupabaseAuthClient:
    def __init__(self, supabase_url: str, anon_key: str, http: httpx.AsyncClient) -> None:
        self._base = f"{supabase_url.rstrip('/')}/auth/v1"
        self._headers = {"apikey": anon_key}
        self._http = http

    def authorize_url(self, *, provider: str, redirect_to: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "provider": provider,
                "redirect_to": redirect_to,
                "code_challenge": code_challenge,
                "code_challenge_method": "s256",
            }
        )
        return f"{self._base}/authorize?{query}"

    async def exchange_code(self, code: str, code_verifier: str) -> AuthSession:
        return await self._token("pkce", {"auth_code": code, "code_verifier": code_verifier})

    async def refresh(self, refresh_token: str) -> AuthSession:
        return await self._token("refresh_token", {"refresh_token": refresh_token})

    async def sign_out(self, access_token: str) -> None:
        # Best effort: the local cookies are cleared regardless.
        with contextlib.suppress(httpx.HTTPError):
            await self._http.post(
                f"{self._base}/logout",
                headers={**self._headers, "Authorization": f"Bearer {access_token}"},
            )

    async def fetch_jwks(self) -> dict[str, Any]:
        response = await self._http.get(f"{self._base}/.well-known/jwks.json")
        response.raise_for_status()
        return response.json()

    async def _token(self, grant_type: str, payload: dict[str, str]) -> AuthSession:
        try:
            response = await self._http.post(
                f"{self._base}/token",
                params={"grant_type": grant_type},
                json=payload,
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise AuthError(str(exc)) from exc
        if response.status_code != 200:
            raise AuthError(f"{grant_type} failed with HTTP {response.status_code}")
        data = response.json()
        return AuthSession(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_in=int(data.get("expires_in", 3600)),
        )
