"""Who is making the request.

Every caller must present a Firebase ID token, and its signature, expiry,
audience and issuer are checked before any claim is read; see ``tokens.py``.
The owner is the token's ``sub``, the Firebase account id. A caller is an admin
when the signed ``admin`` claim is boolean true, when their verified email is
listed in ``ADMIN_EMAILS``, or when the owner string itself is listed there.
Without a valid token the answer is 401. When ``ACCESS_LIST`` is set, a caller
whose verified email matches none of its regexes (and who is not an admin) gets
403 from every route except ``/player``, which reports ``allowed: false``.
"""

import logging
import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from orchestrator.tokens import InvalidToken, VerifierUnavailable

logger = logging.getLogger("orchestrator.auth")


@dataclass(frozen=True)
class Caller:
    owner: str
    admin: bool
    allowed: bool = True


ACCESS_DENIED = "you do not have access to this service"


def identify_caller(
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
    settings = request.app.state.settings
    admin = _is_admin(claims, owner, settings.admin_emails)
    allowed = admin or _is_allowed(claims, settings.access_list)
    return Caller(owner, admin, allowed)


def get_caller(caller: Annotated[Caller, Depends(identify_caller)]) -> Caller:
    if not caller.allowed:
        raise HTTPException(403, ACCESS_DENIED)
    return caller


def get_owner(caller: Annotated[Caller, Depends(get_caller)]) -> str:
    return caller.owner


Owner = Annotated[str, Depends(get_owner)]
CallerDep = Annotated[Caller, Depends(get_caller)]


def require_admin(caller: CallerDep) -> None:
    if not caller.admin:
        raise HTTPException(403, "not an admin")


AdminRequired = Annotated[None, Depends(require_admin)]


def _is_allowed(claims: dict, access_list: tuple[re.Pattern[str], ...] | None) -> bool:
    if access_list is None:
        return True
    email = claims.get("email")
    if not isinstance(email, str) or claims.get("email_verified") is not True:
        return False
    return any(pattern.fullmatch(email) for pattern in access_list)


def _is_admin(claims: dict, owner: str, emails: frozenset[str]) -> bool:
    """A signed token's email counts only when Firebase marks it verified."""

    if owner in emails:
        return True
    if claims.get("admin") is True:
        return True
    email = claims.get("email")
    return isinstance(email, str) and email in emails and claims.get("email_verified") is True


IdentifiedDep = Annotated[Caller, Depends(identify_caller)]
