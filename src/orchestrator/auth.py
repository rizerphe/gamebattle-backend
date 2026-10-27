"""Who is making the request.

Every caller must present a Firebase ID token, and its signature, expiry,
audience and issuer are checked before any claim is read; see ``tokens.py``.
The owner is the token's ``sub``, the Firebase account id. A caller is an admin
when the signed ``admin`` claim is boolean true, when their verified email is
listed in ``ADMIN_EMAILS``, or when the owner string itself is listed there.
Without a valid token the answer is 401.
"""

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from orchestrator.tokens import InvalidToken, VerifierUnavailable

logger = logging.getLogger("orchestrator.auth")


@dataclass(frozen=True)
class Caller:
    owner: str
    admin: bool


def get_caller(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Caller:
    if not authorization:
        raise HTTPException(401, "authentication required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "invalid bearer token")
    try:
        claims = request.app.state.verifier.verify(token.strip())
    except InvalidToken as error:
        raise HTTPException(401, "invalid bearer token") from error
    except VerifierUnavailable as error:
        logger.error("could not verify a token: %s", error)
        raise HTTPException(503, "authentication is unavailable") from error
    owner = claims["sub"]
    return Caller(owner, _is_admin(claims, owner, request.app.state.settings.admin_emails))


def get_owner(caller: Annotated[Caller, Depends(get_caller)]) -> str:
    return caller.owner


Owner = Annotated[str, Depends(get_owner)]
CallerDep = Annotated[Caller, Depends(get_caller)]


def require_admin(caller: CallerDep) -> None:
    if not caller.admin:
        raise HTTPException(403, "not an admin")


AdminRequired = Annotated[None, Depends(require_admin)]


def _is_admin(claims: dict, owner: str, emails: frozenset[str]) -> bool:
    """A signed token's email counts only when Firebase marks it verified."""

    if owner in emails:
        return True
    if claims.get("admin") is True:
        return True
    email = claims.get("email")
    return isinstance(email, str) and email in emails and claims.get("email_verified") is True
