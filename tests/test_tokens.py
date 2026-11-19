"""TokenVerifier key fetching (regression: fresh containers have a small monotonic clock)."""

import time
import uuid

import pytest

from app.auth.tokens import InvalidToken, TokenVerifier
from tests.conftest import ISSUER, FakeAuthClient, mint_token

USER = uuid.UUID(int=1)


async def test_fetches_keys_on_first_use_even_right_after_boot(monkeypatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 12.0)  # container booted 12 s ago
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return await FakeAuthClient().fetch_jwks()

    verifier = TokenVerifier(fetch, issuer=ISSUER)
    claims = await verifier.verify(mint_token(USER, "a@b.c"))
    assert claims["email"] == "a@b.c"
    assert calls == 1


async def test_unknown_key_refetches_at_most_every_five_minutes(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"keys": []}

    verifier = TokenVerifier(fetch, issuer=ISSUER)
    token = mint_token(USER, "a@b.c")
    for _ in range(2):
        with pytest.raises(InvalidToken):
            await verifier.verify(token)
    assert calls == 1
    now[0] += 301
    with pytest.raises(InvalidToken):
        await verifier.verify(token)
    assert calls == 2
