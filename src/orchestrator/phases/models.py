"""The competition phase.

The game moves through four phases in a fixed order. An admin may schedule when
each phase after ``development`` begins. The current phase is the latest one
whose start has arrived; with nothing scheduled it is ``development``.
"""

from dataclasses import dataclass
from enum import StrEnum

from orchestrator.sessions.models import SessionKind


class Phase(StrEnum):
    DEVELOPMENT = "development"
    PREPARATION = "preparation"
    COMPETITION = "competition"
    REVIEW = "review"


ORDER = tuple(Phase)
SCHEDULED = ORDER[1:]


@dataclass(frozen=True)
class Schedule:
    """Unix seconds at which each phase begins, or ``None`` when unset."""

    preparation: float | None = None
    competition: float | None = None
    review: float | None = None

    def start(self, phase: Phase) -> float | None:
        if phase == Phase.DEVELOPMENT:
            return None
        return getattr(self, phase.value)

    def with_start(self, phase: Phase, at: float | None) -> "Schedule":
        values = {item.value: self.start(item) for item in SCHEDULED}
        values[phase.value] = at
        return Schedule(**values)

    def in_order(self) -> bool:
        """Every set start is no earlier than the set starts before it."""

        previous = None
        for phase in SCHEDULED:
            at = self.start(phase)
            if at is None:
                continue
            if previous is not None and at < previous:
                return False
            previous = at
        return True


@dataclass(frozen=True)
class PhaseState:
    phase: Phase
    schedule: Schedule


def current_phase(schedule: Schedule, now: float) -> Phase:
    current = Phase.DEVELOPMENT
    for phase in SCHEDULED:
        at = schedule.start(phase)
        if at is not None and at <= now:
            current = phase
    return current


def can_edit(phase: Phase) -> bool:
    return phase == Phase.DEVELOPMENT


def can_compare(phase: Phase) -> bool:
    return phase == Phase.COMPETITION


def can_see_leaderboard(phase: Phase) -> bool:
    return phase in (Phase.COMPETITION, Phase.REVIEW)


def allows_session(kind: SessionKind, phase: Phase) -> bool:
    """Whether a session of this kind may be running in ``phase``.

    Admin sessions are a teacher's tool and are not tied to a phase.
    """

    if kind == "admin":
        return True
    if kind == "comparison":
        return can_compare(phase)
    return can_edit(phase)
