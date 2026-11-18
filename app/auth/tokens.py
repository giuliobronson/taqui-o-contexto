"""Verification of Supabase access tokens (asymmetric JWTs published as JWKS)."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

import jwt

JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]

# Unknown key ids trigger a refetch at most this often (keys rotate rarely).
_REFETCH_INTERVAL_SECONDS = 300


class InvalidToken(Exception):
    pass


class TokenVerifier:
    def __init__(self, fetch_jwks: JwksFetcher, *, issuer: str, audience: str = "authenticated"):
        self._fetch_jwks = fetch_jwks
        self._issuer = issuer
        self._audience = audience
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0

    async def verify(self, token: str) -> dict[str, Any]:
        """Return the token's claims, or raise InvalidToken."""
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc

        key = await self._key(kid)
        if key is None:
            raise InvalidToken("Unknown signing key.")
        try:
            return jwt.decode(
                token,
                key.key,
                algorithms=[key.algorithm_name],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc

    async def _key(self, kid: str | None) -> jwt.PyJWK | None:
        if (
            kid not in self._keys
            and time.monotonic() - self._fetched_at > _REFETCH_INTERVAL_SECONDS
        ):
            jwks = await self._fetch_jwks()
            self._keys = {
                key["kid"]: jwt.PyJWK(key) for key in jwks.get("keys", []) if "kid" in key
            }
            self._fetched_at = time.monotonic()
        return self._keys.get(kid) if kid else None


def seconds_until_expiry(token: str) -> float:
    """Seconds left before `token` expires (negative if expired). Signature not checked."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return -1.0
    return float(claims.get("exp", 0)) - time.time()
