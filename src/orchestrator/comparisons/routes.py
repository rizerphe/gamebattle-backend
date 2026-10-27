"""Comparisons over HTTP.

``POST /comparisons`` launches two published games. Each side's list grows when
that game is restarted or reported and replaced. A rating is the result for the
first list and is stored only after both current games have exited on their own.
"""

import math
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, Response

from orchestrator.access import CompareRequired, LeaderboardRequired
from orchestrator.auth import AdminRequired, CallerDep, OptionalCaller, Owner
from orchestrator.comparisons.models import (
    Comparison,
    ComparisonActivity,
    ComparisonView,
    LeaderboardEntry,
)
from orchestrator.comparisons.service import (
    AlreadyRated,
    Comparisons,
    NeedsConfirmation,
    OwnGame,
    Unavailable,
    Unfinished,
)
from orchestrator.dependencies import ComparisonsDep, StoreDep
from orchestrator.files.models import NoEntrypoint
from orchestrator.reports.models import ReportReason
from orchestrator.sessions.service import TooManySessions
from orchestrator.store import Missing
from orchestrator.terminal_client import TerminalError

router = APIRouter()

_MAX_REASON_LENGTH = 4000
# Path segments arrive as strings. ``Literal[0, 1]`` does not coerce ``"0"``.
Side = Annotated[int, Path(ge=0, le=1)]


@dataclass(frozen=True)
class FileReport:
    short_reason: ReportReason
    reason: str = ""
    keep_playing: bool = False


@dataclass(frozen=True)
class Rate:
    score: float
    confirmed: bool = False


@router.get("/leaderboard")
async def read_leaderboard(
    comparisons: ComparisonsDep,
    store: StoreDep,
    caller: OptionalCaller,
    _open: LeaderboardRequired,
) -> list[LeaderboardEntry]:
    # Public while the phase shows it: the leaderboard page works signed out.
    # Entries carry no ids; a signed-in viewer only learns which one is theirs.
    viewer = store.player(caller.owner).id if caller else None
    return await comparisons.leaderboard(viewer)


@router.get("/comparisons")
async def list_comparisons(
    comparisons: ComparisonsDep,
    _admin: AdminRequired,
    player: UUID | None = Query(default=None),
) -> list[ComparisonActivity]:
    return await comparisons.activity(player=player)


@router.post("/comparisons", status_code=201)
async def start_comparison(
    comparisons: ComparisonsDep, owner: Owner, _compare: CompareRequired, response: Response
) -> ComparisonView:
    try:
        comparison, created = await comparisons.start(owner)
    except Unavailable as error:
        raise HTTPException(409, error.message) from error
    except TooManySessions as error:
        raise _too_many() from error
    except NoEntrypoint as error:
        raise HTTPException(400, "snapshot needs main.py, or exactly one .py file") from error
    except TerminalError as error:
        raise _terminal_error(error) from error
    if not created:
        response.status_code = 200
    return await comparisons.present(comparison)


@router.get("/comparisons/{comparison_id}")
async def read_comparison(
    comparison_id: UUID, comparisons: ComparisonsDep, caller: CallerDep
) -> ComparisonView:
    owned = await _owned(comparisons, caller.owner, comparison_id, any_player=caller.admin)
    return await comparisons.present(owned)


@router.post("/comparisons/{comparison_id}/sides/{side}/restart")
async def restart(
    comparison_id: UUID,
    side: Side,
    comparisons: ComparisonsDep,
    owner: Owner,
    _compare: CompareRequired,
) -> ComparisonView:
    try:
        comparison = await comparisons.restart(owner, comparison_id, side)
    except AlreadyRated as error:
        raise HTTPException(409, "comparison already rated") from error
    except Missing as error:
        raise HTTPException(404, "comparison not found") from error
    except TooManySessions as error:
        raise _too_many() from error
    except NoEntrypoint as error:
        raise HTTPException(400, "snapshot needs main.py, or exactly one .py file") from error
    except TerminalError as error:
        raise _terminal_error(error) from error
    return await comparisons.present(comparison)


@router.post("/comparisons/{comparison_id}/sides/{side}/report")
async def report(
    comparison_id: UUID,
    side: Side,
    body: FileReport,
    comparisons: ComparisonsDep,
    owner: Owner,
    _compare: CompareRequired,
) -> ComparisonView:
    reason = body.reason.strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise HTTPException(400, "invalid report reason")
    try:
        comparison = await comparisons.report(
            owner, comparison_id, side, body.short_reason, reason, body.keep_playing
        )
    except AlreadyRated as error:
        raise HTTPException(409, "comparison already rated") from error
    except Unavailable as error:
        raise HTTPException(409, error.message) from error
    except OwnGame as error:
        raise HTTPException(403, "you cannot report your own game") from error
    except TooManySessions as error:
        raise _too_many() from error
    except Missing as error:
        raise HTTPException(404, "comparison not found") from error
    except TooManySessions as error:
        raise _too_many() from error
    except NoEntrypoint as error:
        raise HTTPException(400, "snapshot needs main.py, or exactly one .py file") from error
    except TerminalError as error:
        raise _terminal_error(error) from error
    return await comparisons.present(comparison)


@router.put("/comparisons/{comparison_id}/rating")
async def rate(
    comparison_id: UUID,
    body: Rate,
    comparisons: ComparisonsDep,
    owner: Owner,
    _compare: CompareRequired,
) -> ComparisonView:
    if not math.isfinite(body.score) or not 0 <= body.score <= 1:
        raise HTTPException(400, "invalid rating")
    try:
        comparison = await comparisons.rate(
            owner, comparison_id, body.score, body.confirmed
        )
    except Unfinished as error:
        raise HTTPException(409, "games have not finished") from error
    except NeedsConfirmation as error:
        raise HTTPException(409, "rating needs confirmation") from error
    except Missing as error:
        raise HTTPException(404, "comparison not found") from error
    except TerminalError as error:
        raise _terminal_error(error) from error
    return await comparisons.present(comparison)


async def _owned(
    comparisons: Comparisons, owner: str, comparison_id: UUID, *, any_player: bool = False
) -> Comparison:
    try:
        return await comparisons.get(owner, comparison_id, any_player=any_player)
    except Missing as error:
        raise HTTPException(404, "comparison not found") from error
    except TerminalError as error:
        raise _terminal_error(error) from error


def _too_many() -> HTTPException:
    return HTTPException(429, "too many open sessions")


def _terminal_error(error: TerminalError) -> HTTPException:
    status = error.status if error.status in (400, 404, 503) else 502
    return HTTPException(status, error.message)
