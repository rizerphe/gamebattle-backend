"""A store for game preferences"""
from __future__ import annotations
from dataclasses import dataclass
import operator
from typing import Iterator, Protocol
import uuid

from gamebattle_backend.common import GameMeta
from gamebattle_backend.launcher import Launcher, Prelauncher

from .session import Session


@dataclass
class Preference:
    """A preference for a game"""

    games: tuple[str, str]
    first_score: float

    @classmethod
    def from_session(cls, session: Session, first_score: float) -> Preference:
        return cls(
            (
                session.games[0].metadata.folder_name,
                session.games[1].metadata.folder_name,
            ),
            first_score,
        )


@dataclass
class Rating:
    """A rating for a game"""

    name: str
    score: float


@dataclass
class Report:
    """A report"""

    session: uuid.UUID
    reason: str
    author: str


class PreferenceStore(Protocol):
    """A store for game preferences"""

    def __getitem__(self, key: uuid.UUID) -> Preference:
        """Get a preference.

        Args:
            key (str): The session id
        """

    def __setitem__(self, key: uuid.UUID, value: Preference) -> None:
        """Set a preference.

        Args:
            key (str): The session id
            value (Preference): The preference
        """

    def __delitem__(self, key: uuid.UUID) -> None:
        """Delete a preference.

        Args:
            key (str): The session id
        """

    def bind(self, rating_system: RatingSystem) -> None:
        """Bind a rating system.

        Args:
            rating_system (RatingSystem): The rating system to bind
        """


class RatingSystem(Protocol):
    """A rating system for games"""

    def register(self, preference: Preference) -> None:
        """Register a preference.

        Args:
            preference (Preference): The preference to register
        """

    def clear(self) -> None:
        """Clear the rating system."""

    def top(self) -> Iterator[Rating]:
        """Get the top games."""

    def score(self, game: str) -> float:
        """Get the score of a game.

        Args:
            game (str): The game to get the score of
        """


class EloRatingSystem:
    def __init__(self, k: float = 32, initial: float = 1000):
        self.k = k
        self.initial = initial
        self.ratings: dict[str, float] = {}
        self.runs: dict[str, int] = {}
        self.planned_pairs: set[frozenset[str]] = set()
        self.reports: dict[str, list[Report]] = {}

    def clear(self) -> None:
        self.ratings.clear()
        self.runs.clear()

    def register(self, preference: Preference) -> None:
        for game in preference.games:
            if game not in self.ratings:
                self.ratings[game] = self.initial
        expecteds = self.expected(preference)
        for i, (game, expected) in enumerate(zip(preference.games, expecteds)):
            actual = preference.first_score if i == 0 else 1 - preference.first_score
            self.ratings[game] += self.k * (actual - expected)
            self.runs[game] = self.runs.get(game, 0) + 1

    def expected(self, preference: Preference) -> tuple[float, float]:
        return (
            self.expected_score(preference.games[0], preference.games[1]),
            self.expected_score(preference.games[1], preference.games[0]),
        )

    def expected_score(self, game: str, other: str) -> float:
        return 1 / (1 + 10 ** ((self.ratings[other] - self.ratings[game]) / 400))

    def top(self) -> Iterator[Rating]:
        return iter(
            sorted(
                (Rating(game, score) for game, score in self.ratings.items()),
                key=operator.attrgetter("score"),
                reverse=True,
            )
        )

    def score(self, game: str) -> float:
        return self.ratings[game]

    def launch(self, launcher: Launcher, capacity: int, owner: str) -> list[GameMeta]:
        available = [game for game in launcher.games if game.email != owner]
        if capacity % 2:
            capacity += 1
        game_pairs = [
            (game, other)
            for game in available
            for other in available
            if game != other
            and frozenset({game.folder_name, other.folder_name})
            not in self.planned_pairs
        ]
        if not game_pairs:
            return []
        game_pairs.sort(
            key=lambda pair: self.pair_likelihood(
                pair[0].folder_name, pair[1].folder_name
            ),
            reverse=True,
        )
        self.planned_pairs.update(
            frozenset({game_pair[0].folder_name, game_pair[1].folder_name})
            for game_pair in game_pairs[: capacity // 2]
        )
        return [game for pair in game_pairs[: capacity // 2] for game in pair]

    def pair_likelihood(self, game: str, other: str) -> float:
        return abs(
            self.ratings.get(game, self.initial) - self.ratings.get(other, self.initial)
        ) / 200 - (self.runs.get(game, 0) + self.runs.get(other, 0))

    def launch_preloaded(
        self, launcher: Prelauncher, capacity: int, owner: str
    ) -> list[GameMeta]:
        available = [
            game
            for game in launcher.games
            if game.email != owner
            and owner
            not in [report.author for report in self.reports.get(game.folder_name, [])]
        ]
        game_pairs = [
            (game, other) for game in available for other in available if game != other
        ]
        game_pairs.sort(
            key=lambda pair: self.pair_likelihood(
                pair[0].folder_name, pair[1].folder_name
            ),
            reverse=True,
        )
        planned_game_pairs = [
            pair
            for pair in game_pairs
            if frozenset({pair[0].folder_name, pair[1].folder_name})
        ]
        non_planned_game_pairs = [
            pair for pair in game_pairs if pair not in planned_game_pairs
        ]
        pairs_to_launch = (
            planned_game_pairs
            + non_planned_game_pairs[: capacity // 2 - len(planned_game_pairs)]
        )
        for pair in pairs_to_launch:
            frozen = frozenset({pair[0].folder_name, pair[1].folder_name})
            if frozen in self.planned_pairs:
                self.planned_pairs.remove(frozen)
        return ([game for pair in pairs_to_launch for game in pair] + launcher.games)[
            :capacity
        ]

    def report(self, game: GameMeta, report: Report) -> list[Report] | None:
        if game.email == report.author:
            return None
        if report.session in [
            report.session for report in self.reports.get(game.folder_name, [])
        ]:
            return None
        self.reports[game.folder_name] = self.reports.get(game.folder_name, []) + [
            report
        ]
        return self.reports[game.folder_name]


class RAMPreferenceStore:
    def __init__(self):
        self.preferences: dict[uuid.UUID, Preference] = {}
        self.rating_systems: list[RatingSystem] = []

    def __getitem__(self, key: uuid.UUID) -> Preference:
        return self.preferences[key]

    def __setitem__(self, key: uuid.UUID, value: Preference) -> None:
        preference_exists = key in self.preferences
        self.preferences[key] = value
        if preference_exists:
            self.rebuild()
        else:
            for rating_system in self.rating_systems:
                rating_system.register(value)

    def __delitem__(self, key: uuid.UUID) -> None:
        del self.preferences[key]
        self.rebuild()

    def __iter__(self) -> Iterator[Preference]:
        return iter(self.preferences.values())

    def bind(self, rating_system: RatingSystem) -> None:
        for preference in self:
            rating_system.register(preference)
        self.rating_systems.append(rating_system)

    def rebuild(self) -> None:
        for rating_system in self.rating_systems:
            rating_system.clear()
            for preference in self:
                rating_system.register(preference)
