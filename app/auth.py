import secrets

from fastapi import HTTPException, Request, status


async def require_token(request: Request) -> None:
    expected: str = request.app.state.settings.app_auth_token
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not secrets.compare_digest(token, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
