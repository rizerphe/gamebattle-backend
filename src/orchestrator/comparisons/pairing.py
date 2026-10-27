"""Which pair to show, and how one vote moves Elo.

The public rating is the ordinary Elo step with K = 32. Pairing does not refit
it. It treats the current Elo as the mean and the errors as independent, with
variance ``4 / (plays + 1)`` in logit units: a toss-up against a known opponent
is information 1/4, and the starting rating counts as one such observation.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

INITIAL_ELO = 1000.0
K = 32.0


@dataclass(frozen=True)
class RatedGame:
    id: UUID
    elo: float
    plays: int


def usefulness(left: RatedGame, right: RatedGame) -> float:
    """How much total variance a comparison of these two would remove."""

    p = 1 / (1 + 10 ** ((right.elo - left.elo) / 400))
    w = p * (1 - p)
    vi = 4 / (left.plays + 1)
    vj = 4 / (right.plays + 1)
    return w * (vi * vi + vj * vj) / (1 + w * (vi + vj))


def updated_elos(elo_i: float, elo_j: float, score: float) -> tuple[float, float]:
    """Elo after a result ``score`` for ``i``. The two changes sum to zero."""

    expected = 1 / (1 + 10 ** ((elo_j - elo_i) / 400))
    delta = K * (score - expected)
    return elo_i + delta, elo_j - delta


def choose_pair(games: Sequence[RatedGame], rng: random.Random) -> tuple[RatedGame, RatedGame]:
    """The eligible pair with the greatest usefulness. Ties are random.

    The returned order puts the lower id first, so a cold start does not also
    flip which side a score refers to.
    """

    best = -1.0
    tied: list[tuple[RatedGame, RatedGame]] = []
    for index, left in enumerate(games):
        for right in games[index + 1 :]:
            score = usefulness(left, right)
            if score > best:
                best = score
                tied = [(left, right)]
            elif score == best:
                tied.append((left, right))
    left, right = rng.choice(tied)
    return (left, right) if left.id < right.id else (right, left)


def choose_opponent(
    current: RatedGame, candidates: Sequence[RatedGame], rng: random.Random
) -> RatedGame:
    """The candidate that makes the best pair with ``current``. Ties are random."""

    best = -1.0
    tied: list[RatedGame] = []
    for candidate in candidates:
        score = usefulness(current, candidate)
        if score > best:
            best = score
            tied = [candidate]
        elif score == best:
            tied.append(candidate)
    return rng.choice(tied)
