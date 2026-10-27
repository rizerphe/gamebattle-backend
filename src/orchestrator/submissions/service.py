"""Import an archive: one write submits and publishes every game in it.

Validation and Firebase lookup both finish before the first write. A bad zip
or an unreachable directory therefore leaves the database untouched. A missing
account does not: that game still gets a UUID and is reported.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from orchestrator.avatars.metadata import record_firebase_photo
from orchestrator.comparisons.mongo import MongoComparisons, MongoRatings
from orchestrator.comparisons.pairing import INITIAL_ELO
from orchestrator.reports.mongo import MongoReports
from orchestrator.files.models import GameFile
from orchestrator.store import Store
from orchestrator.submissions.archive import ParsedGame, parse_archive
from orchestrator.submissions.firebase import (
    FirebaseProfile,
    UserDirectory,
)

UNLINKED = "firebase user not found"
ARCHIVE_IMPORT_AUTHOR = uuid5(NAMESPACE_URL, "gamebattle:archive-import")


@dataclass(frozen=True)
class SubmittedGame:
    email: str
    name: str
    game_id: UUID
    snapshot_id: UUID
    linked: bool
    warning: str | None


@dataclass(frozen=True)
class SubmissionResult:
    games: tuple[SubmittedGame, ...]
    unlinked: tuple[SubmittedGame, ...]


@dataclass(frozen=True)
class StudentProfile:
    """Display name, photo, and address for a player UUID."""

    id: UUID
    email: str | None
    username: str | None
    photo: str | None


@dataclass(frozen=True)
class AdminStudent:
    """Everything the admin students spreadsheet needs in one row."""

    id: UUID
    game_name: str | None
    email: str | None
    username: str | None
    photo: str | None
    elo: float | None
    place: int | None
    times_played: int
    comparisons_made: int
    reports_filed: int
    reports_received: int
    admin: bool = False


class MissingStudent(Exception):
    """This UUID is not linked to a Firebase account."""


def owner_key(email: str, firebase_uid: str | None) -> str:
    """Owner string hashed into the player UUID.

    A Firebase account id matches the token ``sub`` of a later sign-in. A
    missing account still gets a UUID; the address only feeds that hash, and
    is not itself the id.
    """

    if firebase_uid is None:
        return f"dummy:{email}"
    return firebase_uid


async def submit_archive(
    store: Store,
    blob: bytes,
    *,
    author: UUID,
    users: UserDirectory,
) -> SubmissionResult:
    """Submit and publish every game. ``author`` is recorded on the import."""

    parsed = parse_archive(blob)
    resolved = [(game, await users.lookup(game.email)) for game in parsed]
    submitted = tuple(
        [await _import(store, game, uid, author) for game, uid in resolved]
    )
    return SubmissionResult(
        games=submitted,
        unlinked=tuple(game for game in submitted if not game.linked),
    )


async def _import(
    store: Store, game: ParsedGame, firebase_uid: str | None, author: UUID
) -> SubmittedGame:
    player = store.player(owner_key(game.email, firebase_uid))
    await store.db.players.update_one(
        {"_id": player.id},
        {
            "$set": {
                "name": game.name,
                "email": game.email,
                "firebase_uid": firebase_uid,
                "dummy": firebase_uid is None,
            }
        },
        upsert=True,
    )
    snapshot = await player.files.submit(
        tuple(
            GameFile(path=path, saved_content=content) for path, content in game.files
        ),
        author=author,
    )
    warning = None if firebase_uid is not None else UNLINKED
    return SubmittedGame(
        email=game.email,
        name=game.name,
        game_id=player.id,
        snapshot_id=snapshot.id,
        linked=firebase_uid is not None,
        warning=warning,
    )


def _stored_email(document: dict | None) -> str | None:
    if document is None:
        return None
    email = document.get("email")
    return email if isinstance(email, str) else None


async def _profile_from_account(
    store: Store,
    student_id: UUID,
    stored_email: str | None,
    account: FirebaseProfile,
) -> StudentProfile:
    await record_firebase_photo(store, student_id, account.photo)
    return StudentProfile(
        id=student_id,
        email=stored_email or account.email,
        username=account.username,
        photo=account.photo,
    )


async def student_profile(
    store: Store, users: UserDirectory, student_id: UUID
) -> StudentProfile:
    """Name and photo for a player UUID. The address is not involved."""

    # A player who signed in themselves has no stored account id, so this
    # also finds them by matching the Firebase roster.
    found = await lookup_students(store, users, [student_id])
    if not found:
        raise MissingStudent(student_id)
    return found[0]


async def lookup_students(
    store: Store, users: UserDirectory, student_ids: Sequence[UUID]
) -> list[StudentProfile]:
    """Resolve player UUIDs for admin views such as development history."""

    wanted = list(dict.fromkeys(student_ids))
    if not wanted:
        return []

    rows: dict[UUID, StudentProfile] = {}
    remaining = list(wanted)
    if ARCHIVE_IMPORT_AUTHOR in remaining:
        rows[ARCHIVE_IMPORT_AUTHOR] = StudentProfile(
            id=ARCHIVE_IMPORT_AUTHOR,
            email=None,
            username="Archive import",
            photo=None,
        )
        remaining = [student_id for student_id in remaining if student_id != ARCHIVE_IMPORT_AUTHOR]

    documents = await store.db.players.find(
        {"_id": {"$in": remaining}},
        {"firebase_uid": 1, "email": 1},
    ).to_list()
    by_id = {document["_id"]: document for document in documents}
    uids = [
        uid
        for document in documents
        if isinstance((uid := document.get("firebase_uid")), str) and uid
    ]
    accounts = await users.profiles(tuple(dict.fromkeys(uids))) if uids else {}

    unresolved: set[UUID] = set()
    for student_id in remaining:
        document = by_id.get(student_id)
        stored_email = _stored_email(document)
        uid = document.get("firebase_uid") if document else None
        if isinstance(uid, str) and uid and uid in accounts:
            rows[student_id] = await _profile_from_account(
                store, student_id, stored_email, accounts[uid]
            )
            continue
        if stored_email:
            rows[student_id] = StudentProfile(
                id=student_id,
                email=stored_email,
                username=None,
                photo=None,
            )
            continue
        unresolved.add(student_id)

    if unresolved:
        for student_id, account in (await users.resolve_player_ids(unresolved)).items():
            rows[student_id] = await _profile_from_account(store, student_id, None, account)
            unresolved.discard(student_id)

    return [rows[student_id] for student_id in wanted if student_id in rows]


async def admin_students(
    store: Store, users: UserDirectory, admin_emails: frozenset[str] = frozenset()
) -> list[AdminStudent]:
    games = await store.list_games()
    comparisons = await MongoComparisons(store.db).list_all()
    reports = await MongoReports(store.db).all()
    ratings = await MongoRatings(store.db).all_plays()

    comparisons_made: dict[UUID, int] = defaultdict(int)
    for comparison in comparisons:
        if comparison.rating is not None:
            comparisons_made[comparison.player] += 1

    reports_filed: dict[UUID, int] = defaultdict(int)
    reports_received: dict[UUID, int] = defaultdict(int)
    for report in reports:
        reports_filed[report.session.player] += 1
        reports_received[report.session.game] += 1

    found = await lookup_students(store, users, [game.id for game in games])
    profiles = {profile.id: profile for profile in found}

    ranked = sorted(
        (
            (game_id, ratings[game_id][0])
            for game_id, (_elo, plays) in ratings.items()
            if plays > 0
        ),
        key=lambda item: (-item[1], str(item[0])),
    )
    places = {game_id: index for index, (game_id, _elo) in enumerate(ranked, start=1)}

    rows: list[AdminStudent] = []
    for game in games:
        profile = profiles.get(game.id)
        elo_value, plays = ratings.get(game.id, (INITIAL_ELO, 0))
        rows.append(
            AdminStudent(
                id=game.id,
                game_name=game.name,
                email=profile.email if profile else None,
                username=profile.username if profile else None,
                photo=profile.photo if profile else None,
                elo=elo_value if plays > 0 else None,
                place=places.get(game.id),
                times_played=plays,
                comparisons_made=comparisons_made[game.id],
                reports_filed=reports_filed[game.id],
                reports_received=reports_received[game.id],
                admin=bool(profile and profile.email in admin_emails),
            )
        )

    def sort_key(row: AdminStudent) -> tuple:
        label = (
            row.username
            or row.email
            or row.game_name
            or str(row.id)
        ).casefold()
        return (label, row.id)

    return sorted(rows, key=sort_key)
