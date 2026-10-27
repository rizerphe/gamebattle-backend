"""Verification of Firebase ID tokens.

A token is accepted only when its RS256 signature matches one of Google's
published ``securetoken`` certificates, it has not expired, its audience is
this Firebase project, and its issuer is that project's token issuer. Nothing
in an unverified token is ever read.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.auth import exceptions as google_exceptions
from google.auth import jwt
from google.auth.transport import requests as google_requests

CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)

_MIN_CERT_TTL = 60.0
_MAX_CERT_TTL = 24 * 3600.0
_DEFAULT_CERT_TTL = 3600.0
_UNKNOWN_KEY_REFETCH = 60.0
_CLOCK_SKEW_SECONDS = 10
_FETCH_TIMEOUT_SECONDS = 10


class InvalidToken(Exception):
    """The token is not a currently valid ID token for this project."""


class VerifierUnavailable(Exception):
    """The signing certificates could not be fetched, so nothing can be verified."""


@dataclass(frozen=True)
class Certificates:
    keys: dict[str, str]
    ttl: float


def fetch_google_certificates() -> Certificates:
    """Google's current signing certificates, keyed by key id."""

    try:
        response = google_requests.Request()(
            CERTS_URL, method="GET", timeout=_FETCH_TIMEOUT_SECONDS
        )
    except google_exceptions.TransportError as error:
        raise VerifierUnavailable("could not fetch signing certificates") from error
    if response.status != 200:
        raise VerifierUnavailable(
            f"signing certificates returned status {response.status}"
        )
    try:
        keys = json.loads(response.data)
    except ValueError as error:
        raise VerifierUnavailable("signing certificates were not JSON") from error
    if not isinstance(keys, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in keys.items()
    ):
        raise VerifierUnavailable("signing certificates had an unexpected shape")
    return Certificates(keys, _ttl(response.headers.get("cache-control", "")))


def _ttl(cache_control: str) -> float:
    for part in cache_control.split(","):
        name, _, value = part.strip().partition("=")
        if name.lower() == "max-age":
            try:
                seconds = float(value)
            except ValueError:
                break
            return min(max(seconds, _MIN_CERT_TTL), _MAX_CERT_TTL)
    return _DEFAULT_CERT_TTL


class FirebaseTokenVerifier:
    def __init__(
        self,
        project_id: str,
        fetch: Callable[[], Certificates] = fetch_google_certificates,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not project_id:
            raise ValueError("a Firebase project id is required to verify tokens")
        self._project_id = project_id
        self._issuer = f"https://securetoken.google.com/{project_id}"
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, str] = {}
        self._fetched_at: float | None = None
        self._ttl = 0.0

    def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.decode_header(token)
        except (ValueError, google_exceptions.GoogleAuthError) as error:
            raise InvalidToken("malformed token") from error
        if header.get("alg") != "RS256":
            raise InvalidToken("unexpected signing algorithm")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise InvalidToken("token has no key id")

        keys = self._keys_for(key_id)
        try:
            claims = jwt.decode(
                token,
                certs=keys,
                audience=self._project_id,
                clock_skew_in_seconds=_CLOCK_SKEW_SECONDS,
            )
        except (ValueError, google_exceptions.GoogleAuthError) as error:
            raise InvalidToken("token rejected") from error

        if claims.get("iss") != self._issuer:
            raise InvalidToken("wrong issuer")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 128:
            raise InvalidToken("token has no subject")
        return claims

    def _keys_for(self, key_id: str) -> dict[str, str]:
        with self._lock:
            now = self._clock()
            age = None if self._fetched_at is None else now - self._fetched_at
            stale = age is None or age >= self._ttl
            unknown = key_id not in self._keys
            if stale or (unknown and age is not None and age >= _UNKNOWN_KEY_REFETCH):
                fresh = self._fetch()
                self._keys = fresh.keys
                self._ttl = fresh.ttl
                self._fetched_at = now
            if key_id not in self._keys:
                raise InvalidToken("unknown signing key")
            return {key_id: self._keys[key_id]}
