"""The teacher's view of fairness: suspicious sessions, activity, and what-ifs.

Only sessions that can still change a result are listed: those on a side whose
best session is not ``enough``, those a teacher has already judged, and those
whose check failed (so a broken provider shows up). Test and admin runs are
never listed, because everything here starts from rated comparisons.
"""

from dataclasses import dataclass
from uuid import UUID

from orchestrator.comparisons.service import Comparisons, exited_sides
from orchestrator.fairness.verdicts import Effective, effective, side_counts, side_is_flagged
from orchestrator.sessions.models import FairnessOverride


@dataclass(frozen=True)
class FairnessRow:
    session_id: UUID
    comparison_id: UUID
    side: int
    voter: UUID
    voter_email: str | None
    game_id: UUID
    game_name: str | None
    started_at: float
    rated_at: float | None
    rating: float
    counted: bool
    confirmed_warning: bool
    duration: float | None
    status: str | None
    verdict: str | None
    reason: str | None
    override: FairnessOverride | None
    effective: Effective


@dataclass(frozen=True)
class RatedActivity:
    comparison_id: UUID
    voter: UUID
    voter_email: str | None
    started_at: float
    rated_at: float | None
    rating: float
    counted: bool
    confirmed_warning: bool
    game_names: tuple[str | None, str | None]


@dataclass(frozen=True)
class HypotheticalEntry:
    game_id: UUID
    name: str | None
    current_score: float | None
    hypothetical_score: float | None
    delta: float | None
    owned_by_excluded: bool


async def _profiles(comparisons: Comparisons, ids: set[UUID]) -> dict[UUID, dict]:
    docs = await comparisons.store.db.players.find(
        {"_id": {"$in": list(ids)}}, {"email": 1, "name": 1}
    ).to_list()
    return {doc["_id"]: doc for doc in docs}


def _text(doc: dict | None, key: str) -> str | None:
    value = doc.get(key) if doc else None
    return value if isinstance(value, str) else None


async def rows(comparisons: Comparisons) -> list[FairnessRow]:
    rated = await comparisons.records.list_rated()
    sessions = await comparisons.sessions.get_many(
        session_id for c in rated for side in c.sessions for session_id in side
    )
    profiles = await _profiles(
        comparisons,
        {c.player for c in rated} | {s.game for s in sessions.values()},
    )
    found: list[FairnessRow] = []
    for comparison in rated:
        if comparison.rating is None:
            continue
        sides = exited_sides(comparison, sessions)
        counted = all(side_counts(side) for side in sides)
        for index, side in enumerate(sides):
            flagged = side_is_flagged(side)
            for session in side:
                failed = session.fairness is not None and session.fairness.verdict is None
                if not (flagged or session.fairness_override is not None or failed):
                    continue
                fairness = session.fairness
                found.append(
                    FairnessRow(
                        session_id=session.id,
                        comparison_id=comparison.id,
                        side=index,
                        voter=comparison.player,
                        voter_email=_text(profiles.get(comparison.player), "email"),
                        game_id=session.game,
                        game_name=_text(profiles.get(session.game), "name"),
                        started_at=session.started_at,
                        rated_at=comparison.rated_at,
                        rating=comparison.rating,
                        counted=counted,
                        confirmed_warning=comparison.confirmed_warning,
                        duration=fairness.duration if fairness else None,
                        status=fairness.status if fairness else None,
                        verdict=fairness.verdict if fairness else None,
                        reason=fairness.reason if fairness else None,
                        override=session.fairness_override,
                        effective=effective(session),
                    )
                )
    return found


async def activity(comparisons: Comparisons) -> list[RatedActivity]:
    rated = await comparisons.records.list_rated()
    sessions = await comparisons.sessions.get_many(
        session_id for c in rated for side in c.sessions for session_id in side
    )
    profiles = await _profiles(
        comparisons,
        {c.player for c in rated} | {s.game for s in sessions.values()},
    )
    entries = []
    for comparison in rated:
        if comparison.rating is None:
            continue
        sides = exited_sides(comparison, sessions)
        names = tuple(
            _text(profiles.get(sessions[side[-1]].game), "name")
            for side in comparison.sessions
        )
        entries.append(
            RatedActivity(
                comparison_id=comparison.id,
                voter=comparison.player,
                voter_email=_text(profiles.get(comparison.player), "email"),
                started_at=comparison.started_at,
                rated_at=comparison.rated_at,
                rating=comparison.rating,
                counted=all(side_counts(side) for side in sides),
                confirmed_warning=comparison.confirmed_warning,
                game_names=(names[0], names[1]),
            )
        )
    return entries


async def hypothetical(
    comparisons: Comparisons, exclude_players: set[UUID]
) -> list[HypotheticalEntry]:
    """The leaderboard as it would be without these players' ratings."""

    current = await comparisons.ratings.all_plays()
    without = await comparisons.compute_ratings(exclude_players)
    profiles = await _profiles(comparisons, set(current) | set(without))
    entries = []
    for game_id in current.keys() | without.keys():
        now = current.get(game_id)
        then = without.get(game_id)
        entries.append(
            HypotheticalEntry(
                game_id=game_id,
                name=_text(profiles.get(game_id), "name"),
                current_score=now[0] if now else None,
                hypothetical_score=then[0] if then else None,
                delta=(then[0] if then else 1000.0) - (now[0] if now else 1000.0),
                owned_by_excluded=game_id in exclude_players,
            )
        )
    entries.sort(key=lambda e: (-(e.hypothetical_score or 0), e.name or ""))
    return entries
