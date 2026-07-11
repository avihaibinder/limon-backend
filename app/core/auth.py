"""Supabase JWT verification and just-in-time user provisioning.

The client signs in with Supabase Auth (Google OAuth) and sends the resulting
access token as a Bearer header. We verify it against the project's JWKS
endpoint (or the legacy HS256 shared secret) and resolve it to a local User
row, creating one on first sight — there is no separate sign-up call.
"""

import ssl
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.core.config import get_settings
from app.dependencies import SessionDep
from app.models.user import User
from app.services import users as users_service

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache
def _jwks_client(supabase_url: str) -> PyJWKClient:
    # Certificates are still fully verified, but without Python 3.13's
    # VERIFY_X509_STRICT default — TLS-inspecting middleboxes (corporate
    # proxies, antivirus) present slightly non-conformant CA certs that
    # strict mode rejects, which would 401 every valid token on such machines.
    ssl_context = ssl.create_default_context()
    ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    # PyJWKClient caches fetched keys; the blocking HTTP call happens only on
    # the first request after startup and again on key rotation.
    return PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json", ssl_context=ssl_context)


def decode_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return its claims (401 on failure)."""
    settings = get_settings()
    if settings.supabase_url is None:
        # Deliberate 500: the deployment is misconfigured, the caller did nothing wrong.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is not configured (set LIMON_SUPABASE_URL).",
        )
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
        if algorithm == "HS256":
            if settings.supabase_jwt_secret is None:
                raise _unauthorized("HS256 tokens are not accepted (no JWT secret configured)")
            key: Any = settings.supabase_jwt_secret
        elif algorithm in ("ES256", "RS256"):
            key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token).key
        else:
            raise _unauthorized(f"Unsupported token algorithm {algorithm!r}")
        return jwt.decode(token, key, algorithms=[algorithm], audience="authenticated")
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the Bearer token to a local User, provisioning it on first sight."""
    if credentials is None:
        raise _unauthorized("Not authenticated")
    claims = decode_token(credentials.credentials)
    app_metadata = claims.get("app_metadata") or {}
    user_metadata = claims.get("user_metadata") or {}
    return await users_service.get_or_create_user(
        session,
        provider=app_metadata.get("provider") or "supabase",
        provider_subject=claims["sub"],
        email=claims.get("email"),
        display_name=user_metadata.get("full_name") or user_metadata.get("name"),
    )


CurrentUserDep = Annotated[User, Depends(get_current_user)]
