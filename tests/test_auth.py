from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import require_token


def _request(auth_header: str | None, expected_token: str = "secret") -> SimpleNamespace:
    headers: dict[str, str] = {}
    if auth_header is not None:
        headers["authorization"] = auth_header
    settings = SimpleNamespace(app_auth_token=expected_token)
    state = SimpleNamespace(settings=settings)
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app, headers=headers)


async def test_require_token_passes_with_correct_bearer():
    await require_token(_request("Bearer secret"))


async def test_require_token_rejects_missing_header():
    with pytest.raises(HTTPException) as exc:
        await require_token(_request(None))
    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


async def test_require_token_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc:
        await require_token(_request("Bearer wrong"))
    assert exc.value.status_code == 401


async def test_require_token_rejects_non_bearer_scheme():
    with pytest.raises(HTTPException) as exc:
        await require_token(_request("Basic secret"))
    assert exc.value.status_code == 401


async def test_require_token_rejects_empty_token():
    with pytest.raises(HTTPException) as exc:
        await require_token(_request("Bearer "))
    assert exc.value.status_code == 401
