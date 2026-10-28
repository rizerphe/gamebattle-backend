"""The versioned filesystem shown by the game editor.

Every editor action creates an immutable snapshot. Snapshots form one linear
history: a new snapshot must be based on the current one and atomically becomes
the new current snapshot.

Each file contains its saved contents and, optionally, an unsaved draft. This
allows an old snapshot to reconstruct both the filesystem visible to the rest
of the application and exactly what the student saw in their editor.

Submitted and published are pointers to clean snapshots. Moving either pointer
does not create a snapshot, and any clean historical snapshot may be selected.
Publishing records when the pointer moved. Reports from five different players
against that snapshot after that moment clear it. Unpublishing clears it outright.

Each snapshot records the player who appended it. That author is the game's
owner unless someone else, such as an admin editing the game, wrote it.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import computed_field


@dataclass(frozen=True)
class FileDraft:
    """An unsaved file state.

    ``content=None`` represents an unsaved deletion. The absence of a
    ``FileDraft`` represents an unmodified file.
    """

    content: bytes | None


@dataclass(frozen=True)
class GameFile:
    path: str
    saved_content: bytes | None
    draft: FileDraft | None = None

    @property
    def modified(self) -> bool:
        return self.draft is not None


class SnapshotReason(StrEnum):
    INITIAL = "initial"
    IMPORT = "import"  # This is for the submission import. Latest of these marked as submitted.
    UPLOAD = "upload"  # And this is a file upload - equivalent to a modify+save
    MODIFY = "modify"
    DELETE = "delete"
    SAVE = "save"
    DISCARD = "discard"


@dataclass(frozen=True)
class Snapshot:
    """One immutable revision of a game's files.

    ``author`` is the player who appended this revision. It matches the game's
    owner unless someone else wrote it.
    """

    id: UUID
    parent_id: UUID | None
    timestamp: float
    author: UUID
    reason: SnapshotReason
    files: tuple[GameFile, ...]

    @computed_field
    @property
    def modified(self) -> bool:
        return any(file.modified for file in self.files)


MAX_FILES = 64


def valid_path(path: str) -> bool:
    """Relative printable-ASCII path, the same rule the editor enforces.

    Spaces are fine inside a name. A name that can start a game is stricter,
    see ``can_launch``.
    """

    parts = path.split("/")
    return bool(
        path
        and path.isascii()
        and path.isprintable()
        and not path.startswith("/")
        and "\\" not in path
        and all(
            part not in ("", ".", "..") and part == part.strip(" ") for part in parts
        )
    )


def can_launch(path: str) -> bool:
    """Whether a file can be a game's entrypoint: a ``.py`` file with no space in its path."""

    return path.endswith(".py") and " " not in path


class RevisionConflict(Exception):
    """``expected_current`` was not the current snapshot."""


class DirtySnapshot(Exception):
    """Published and submitted pointers only land on clean snapshots."""


class TooManyFiles(Exception):
    """A game cannot hold more than ``MAX_FILES`` files."""


class NoEntrypoint(Exception):
    """A snapshot has no ``main.py`` and not exactly one ``.py`` file."""


def saved_files(snapshot: Snapshot) -> list[tuple[str, bytes]]:
    """Saved files a session can launch. Drafts stay in the editor."""

    return [
        (file.path, file.saved_content)
        for file in snapshot.files
        if file.saved_content is not None
    ]


def entrypoint(snapshot: Snapshot) -> str:
    """``main.py`` when present, otherwise the only launchable ``.py`` file.

    A ``.py`` file with a space in its path is never launched, but it may
    still be imported or read by the game.
    """

    paths = [file.path for file in snapshot.files if file.saved_content is not None]
    if "main.py" in paths:
        return "main.py"
    python = [path for path in paths if can_launch(path)]
    if len(python) == 1:
        return python[0]
    raise NoEntrypoint


class GameFiles(Protocol):
    """One player's game files.

    Reading the current snapshot of a player with no history creates the
    initial empty snapshot, so every game has a current snapshot.
    """

    async def get_snapshot(
        self, reference: UUID | Literal["current", "submitted", "published"]
    ) -> Snapshot:
        """A snapshot of this game, by id or by pointer.

        Raises ``KeyError`` when the id is not one of this game's snapshots, or
        when the ``submitted`` or ``published`` pointer is unset.
        """
        ...

    async def get_snapshot_history(
        self,
        *,
        before: UUID | None = None,
        limit: int | None = 100,
    ) -> list[Snapshot]:
        """Snapshots oldest first, ending just before ``before`` or at the current one.

        ``limit`` keeps the newest ones. Raises ``KeyError`` when ``before`` is
        not one of this game's snapshots.
        """
        ...

    async def submit(
        self,
        files: tuple[GameFile, ...],
        *,
        author: UUID,
    ) -> Snapshot:
        """Append these files on top of whatever is current, and point submitted and published at them.

        Doesn't require any knowledge of the current snapshot - this is for the submission import.
        ``author`` is the player the import is attributed to. Raises
        ``DirtySnapshot`` if a file has a draft and ``TooManyFiles`` above ``MAX_FILES``.
        """
        ...

    async def set_file(
        self,
        new_file: GameFile,
        *,
        expected_current: UUID,
        author: UUID,
    ) -> Snapshot:
        """Modify the filesystem: upload file, modify file, delete file, or discard draft. Autodetects reason.

        ``author`` is the player who made the edit, which may not be the game's owner.
        A change that leaves the file as it was appends nothing and returns the
        current snapshot. Raises ``RevisionConflict`` when ``expected_current``
        is not the current snapshot, and ``TooManyFiles`` when a new file would
        exceed ``MAX_FILES``.
        """
        ...

    async def publish(self, snapshot_id: UUID) -> None:
        """Point published at an existing clean snapshot.

        Raises ``KeyError`` for an unknown snapshot and ``DirtySnapshot`` for
        one with drafts.
        """
        ...

    async def unpublish(self) -> None:
        """Clear the published pointer. The game stays down until the next publish."""
        ...

    async def published_revision(self) -> tuple[UUID, float] | None:
        """The published snapshot and when it was published, if the game is up."""
        ...
