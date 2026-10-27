"""Firebase Auth, used only to turn an address into an account and to read it back.

Account lookup sends the address as it was written. Firebase returns the
account id, and that id is what the rest of the system stores. When the
written address is not an account, the directory is searched for the one
account whose address is the same once dots in the local part are ignored.
That search only chooses an account. A later request for the student's name
or photo uses the id alone.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

_SCOPE = "https://www.googleapis.com/auth/identitytoolkit"
_LOOKUP_BATCH = 100


class FirebaseUnavailable(Exception):
    """Lookup failed for a reason other than a missing account."""


@dataclass(frozen=True)
class FirebaseProfile:
    username: str | None
    photo: str | None
    email: str | None = None


class UserDirectory(Protocol):
    async def lookup(self, email: str) -> str | None:
        """The Firebase account id for this address, or ``None`` if there is none."""
        ...

    async def profile(self, uid: str) -> FirebaseProfile | None:
        """Name and photo for an account id, or ``None`` if it is gone."""
        ...

    async def profiles(self, uids: Sequence[str]) -> Mapping[str, FirebaseProfile]:
        """Name and photo for each account id that still exists."""
        ...

    async def resolve_player_ids(
        self, player_ids: set[UUID]
    ) -> Mapping[UUID, FirebaseProfile]:
        """Profiles for player UUIDs keyed by Firebase account id."""
        ...


class FirebaseUsers:
    def __init__(
        self, credentials: dict, project_id: str, http: httpx.AsyncClient
    ) -> None:
        self._credentials = service_account.Credentials.from_service_account_info(
            credentials, scopes=[_SCOPE]
        )
        self._project_id = project_id
        self._http = http
        self._index: dict[tuple[str, str], list[str]] | None = None

    async def lookup(self, email: str) -> str | None:
        users = await self._users({"email": [email]})
        if users:
            uid = users[0].get("localId")
            if not isinstance(uid, str) or not uid:
                raise FirebaseUnavailable
            return uid
        return sole_account(account_key(email), await self._accounts())

    async def profile(self, uid: str) -> FirebaseProfile | None:
        return (await self.profiles([uid])).get(uid)

    async def profiles(self, uids: Sequence[str]) -> Mapping[str, FirebaseProfile]:
        """One lookup per hundred ids. A missing account is omitted."""

        wanted = list(dict.fromkeys(uid for uid in uids if uid))
        found: dict[str, FirebaseProfile] = {}
        for start in range(0, len(wanted), _LOOKUP_BATCH):
            users = await self._users({"localId": wanted[start : start + _LOOKUP_BATCH]})
            for user in users or []:
                uid = user.get("localId")
                if not isinstance(uid, str) or not uid:
                    continue
                found[uid] = _firebase_profile(user)
        return found

    async def resolve_player_ids(
        self, player_ids: set[UUID]
    ) -> Mapping[UUID, FirebaseProfile]:
        """Walk the Firebase roster until every requested id is found or exhausted."""

        remaining = set(player_ids)
        if not remaining:
            return {}
        found: dict[UUID, FirebaseProfile] = {}
        token = await asyncio.to_thread(self._token)
        page: str | None = None
        while remaining:
            params: dict[str, str | int] = {"maxResults": 1000}
            if page is not None:
                params["nextPageToken"] = page
            try:
                response = await self._http.get(
                    "https://identitytoolkit.googleapis.com/v1/projects/"
                    f"{self._project_id}/accounts:batchGet",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=30,
                )
            except httpx.HTTPError as error:
                raise FirebaseUnavailable from error
            if response.status_code != 200:
                raise FirebaseUnavailable
            try:
                body = response.json()
            except ValueError as error:
                raise FirebaseUnavailable from error
            for account in body.get("users") or []:
                uid = account.get("localId")
                if not isinstance(uid, str) or not uid:
                    continue
                player_id = uuid5(NAMESPACE_URL, f"gamebattle:{uid}")
                if player_id not in remaining:
                    continue
                found[player_id] = _firebase_profile(account)
                remaining.remove(player_id)
            page = body.get("nextPageToken")
            if not isinstance(page, str) or not page:
                break
        return found

    async def _users(self, payload: dict) -> list[dict] | None:
        """Matching accounts, or ``None`` when Firebase has no such account."""

        token = await asyncio.to_thread(self._token)
        try:
            response = await self._http.post(
                "https://identitytoolkit.googleapis.com/v1/projects/"
                f"{self._project_id}/accounts:lookup",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=20,
            )
        except httpx.HTTPError as error:
            raise FirebaseUnavailable from error
        if response.status_code == 400 and (
            "EMAIL_NOT_FOUND" in response.text or "USER_NOT_FOUND" in response.text
        ):
            return None
        if response.status_code != 200:
            raise FirebaseUnavailable
        try:
            return response.json().get("users") or []
        except ValueError as error:
            raise FirebaseUnavailable from error

    async def _accounts(self) -> dict[tuple[str, str], list[str]]:
        """Every account, keyed by :func:`account_key`. Loaded once."""

        if self._index is not None:
            return self._index
        token = await asyncio.to_thread(self._token)
        index: dict[tuple[str, str], list[str]] = {}
        page: str | None = None
        while True:
            params: dict[str, str | int] = {"maxResults": 1000}
            if page is not None:
                params["nextPageToken"] = page
            try:
                response = await self._http.get(
                    "https://identitytoolkit.googleapis.com/v1/projects/"
                    f"{self._project_id}/accounts:batchGet",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    timeout=30,
                )
            except httpx.HTTPError as error:
                raise FirebaseUnavailable from error
            if response.status_code != 200:
                raise FirebaseUnavailable
            try:
                body = response.json()
            except ValueError as error:
                raise FirebaseUnavailable from error
            for account in body.get("users") or []:
                address = account.get("email")
                uid = account.get("localId")
                if not isinstance(address, str) or not isinstance(uid, str) or not uid:
                    continue
                index.setdefault(account_key(address), []).append(uid)
            page = body.get("nextPageToken")
            if not isinstance(page, str) or not page:
                break
        self._index = index
        return index

    def _token(self) -> str:
        if not self._credentials.valid:
            self._credentials.refresh(Request())
        token = self._credentials.token
        if not isinstance(token, str) or not token:
            raise FirebaseUnavailable
        return token


def account_key(email: str) -> tuple[str, str]:
    """Local part with dots removed, and the domain, both casefolded.

    This is a search key for a roster address that is not stored as written.
    It is not an id.
    """

    local, _, domain = email.strip().casefold().partition("@")
    return local.replace(".", ""), domain


def sole_account(
    key: tuple[str, str], accounts: Mapping[tuple[str, str], Sequence[str]]
) -> str | None:
    """The one account id for ``key``, or ``None`` when there is not exactly one."""

    hits = accounts.get(key, ())
    if len(hits) == 1:
        return hits[0]
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _firebase_profile(account: dict) -> FirebaseProfile:
    return FirebaseProfile(
        username=_text(account.get("displayName")),
        photo=_text(account.get("photoUrl")),
        email=_text(account.get("email")),
    )
