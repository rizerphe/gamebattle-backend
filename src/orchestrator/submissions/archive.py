"""A zip of finished games.

The zip is read in memory. Members are accepted only when the manifest names
them, so a crafted path cannot escape into anything the process stores.

Layout::

    manifest.json
    games/0/main.py
    games/0/words.txt
    games/1/cicada.py

``manifest.json``:

.. code-block:: json

    {
      "version": 1,
      "games": [
        {
          "email": "student@ucu.edu.ua",
          "name": "Cicada",
          "files": [{ "path": "cicada.py", "sha256": "<hex>" }]
        }
      ]
    }

``games/<index>/`` follows the ``games`` array. Paths use the same rules as the
editor. Each file is at most 1 MiB, a game has at most 64 files, and the saved
bytes of one game stay under 12 MiB so a snapshot document fits in MongoDB.
"""

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import dataclass

from orchestrator.files.models import MAX_FILES, valid_path
from orchestrator.files.routes import MAX_FILE_BYTES

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_GAME_BYTES = 12 * 1024 * 1024
_MAX_NAME_LENGTH = 80
_MAX_EMAIL_LENGTH = 254


class ArchiveRejected(Exception):
    """The zip is not a submission archive. ``detail`` is the HTTP message."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class ParsedGame:
    email: str
    name: str
    files: tuple[tuple[str, bytes], ...]


def parse_archive(blob: bytes) -> tuple[ParsedGame, ...]:
    """Validate the zip and return each game's saved files, paths sorted."""

    if not blob or len(blob) > MAX_ARCHIVE_BYTES:
        raise ArchiveRejected("archive is too large" if blob else "invalid archive")
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as error:
        raise ArchiveRejected("invalid archive") from error

    members = archive.infolist()
    _reject_unsafe_members(members)
    manifest = _manifest(archive)
    expected = _expected_members(manifest)
    found = {info.filename: info for info in members if not info.is_dir()}
    if set(found) != expected:
        raise ArchiveRejected("archive does not match its manifest")

    games: list[ParsedGame] = []
    seen: set[str] = set()
    uncompressed = 0
    for index, game in enumerate(manifest):
        email = _email(game.get("email"))
        folded = email.casefold()
        if folded in seen:
            raise ArchiveRejected("duplicate email")
        seen.add(folded)
        name = _name(game.get("name"))
        files = _files(archive, index, game.get("files"), uncompressed)
        uncompressed += sum(len(content) for _, content in files)
        games.append(ParsedGame(email=email, name=name, files=files))
    if not games:
        raise ArchiveRejected("invalid archive")
    return tuple(games)


def _reject_unsafe_members(members: list[zipfile.ZipInfo]) -> None:
    seen: set[str] = set()
    for info in members:
        name = info.filename
        if info.flag_bits & 0x1:
            raise ArchiveRejected("invalid archive")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ArchiveRejected("invalid archive")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_ISDIR(mode) and not info.is_dir():
            raise ArchiveRejected("invalid archive")
        if name in seen or "\\" in name or name.startswith("/") or ".." in name.split("/"):
            raise ArchiveRejected("invalid archive")
        seen.add(name)


def _manifest(archive: zipfile.ZipFile) -> list[dict]:
    try:
        raw = archive.read("manifest.json")
    except KeyError as error:
        raise ArchiveRejected("invalid archive") from error
    if len(raw) > MAX_FILE_BYTES:
        raise ArchiveRejected("archive is too large")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArchiveRejected("invalid archive") from error
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ArchiveRejected("invalid archive")
    games = data.get("games")
    if not isinstance(games, list) or not games:
        raise ArchiveRejected("invalid archive")
    if any(not isinstance(game, dict) for game in games):
        raise ArchiveRejected("invalid archive")
    return games


def _expected_members(games: list[dict]) -> set[str]:
    expected = {"manifest.json"}
    for index, game in enumerate(games):
        files = game.get("files")
        if not isinstance(files, list) or not files:
            raise ArchiveRejected("invalid archive")
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ArchiveRejected("invalid archive")
            expected.add(f"games/{index}/{item['path']}")
    return expected


def _email(value: object) -> str:
    if not isinstance(value, str):
        raise ArchiveRejected("invalid email")
    # Surrounding whitespace is not part of the address. Account lookup
    # receives the rest as written.
    email = value.strip()
    local, separator, domain = email.partition("@")
    if (
        separator != "@"
        or not local
        or not domain
        or "@" in domain
        or not email.isascii()
        or any(char.isspace() for char in email)
        or len(email) > _MAX_EMAIL_LENGTH
    ):
        raise ArchiveRejected("invalid email")
    return email


def _name(value: object) -> str:
    if not isinstance(value, str):
        raise ArchiveRejected("invalid game name")
    name = value.strip()
    if not name or len(name) > _MAX_NAME_LENGTH:
        raise ArchiveRejected("invalid game name")
    return name


def _files(
    archive: zipfile.ZipFile,
    index: int,
    declared: object,
    already: int,
) -> tuple[tuple[str, bytes], ...]:
    if not isinstance(declared, list) or not declared:
        raise ArchiveRejected("invalid archive")
    if len(declared) > MAX_FILES:
        raise ArchiveRejected("too many files")
    paths: set[str] = set()
    total = 0
    files: list[tuple[str, bytes]] = []
    for item in declared:
        if not isinstance(item, dict):
            raise ArchiveRejected("invalid archive")
        path = item.get("path")
        digest = item.get("sha256")
        if not isinstance(path, str) or not valid_path(path):
            raise ArchiveRejected("invalid file name")
        if path in paths:
            raise ArchiveRejected("invalid archive")
        paths.add(path)
        if not isinstance(digest, str) or len(digest) != 64:
            raise ArchiveRejected("archive does not match its manifest")
        try:
            int(digest, 16)
        except ValueError as error:
            raise ArchiveRejected("archive does not match its manifest") from error
        info = archive.getinfo(f"games/{index}/{path}")
        if info.file_size > MAX_FILE_BYTES or info.file_size < 0:
            raise ArchiveRejected("file is too large")
        total += info.file_size
        if total > MAX_GAME_BYTES or already + total > MAX_ARCHIVE_BYTES:
            raise ArchiveRejected("archive is too large")
        content = archive.read(info)
        if len(content) != info.file_size:
            raise ArchiveRejected("invalid archive")
        if hashlib.sha256(content).hexdigest() != digest.lower():
            raise ArchiveRejected("archive does not match its manifest")
        files.append((path, content))
    return tuple(sorted(files, key=lambda item: item[0]))
