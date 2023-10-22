"""A store for game preferences"""
from __future__ import annotations
from dataclasses import dataclass, field
import operator
import time
from typing import AsyncIterator, Literal, Protocol
import uuid

from gamebattle_backend.common import GameMeta
from gamebattle_backend.launcher import Launcher, Prelauncher

from .session import Session


@dataclass
class Preference:
    """A preference for a game"""

    games: tuple[str, str]
    first_score: float
    author: str
    timestamp: float = field(default_factory=time.time)

    @classmethod
    async def from_session(cls, session: Session, first_score: float) -> Preference:
        return cls(
            (
                session.games[0].metadata.folder_name,
                session.games[1].metadata.folder_name,
            ),
            first_score,
            session.owner,
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
    short_reason: Literal["unclear", "buggy", "other"]
    reason: str
    output: str
    author: str


class PreferenceStore(Protocol):
    """A store for game preferences"""

    async def get(self, key: uuid.UUID) -> Preference | None:
        """Get a preference.

        Args:
            key (str): The session id
        """

    async def set(self, key: uuid.UUID, value: Preference) -> None:
        """Set a preference.

        Args:
            key (str): The session id
            value (Preference): The preference
        """

    async def delete(self, key: uuid.UUID) -> None:
        """Delete a preference.

        Args:
            key (str): The session id
        """

    async def bind(self, rating_system: RatingSystem) -> None:
        """Bind a rating system.

        Args:
            rating_system (RatingSystem): The rating system to bind
        """


class ReportStore(Protocol):
    """A store for reports"""

    async def get(self, key: str, /) -> tuple[Report, ...]:
        """Get a report.

        Args:
            key (str): The game name
        """

    async def append(self, key: str, value: Report, /) -> int:
        """Append a report.

        Args:
            key (str): The game name
            value (Report): The report

        Returns:
            int: The new length of the report list
        """

    async def delete(self, key: str, /) -> None:
        """Delete a report.

        Args:
            key (str): The game name
        """


class RatingSystem(Protocol):
    """A rating system for games"""

    async def register(self, preference: Preference) -> None:
        """Register a preference.

        Args:
            preference (Preference): The preference to register
        """

    async def clear(self) -> None:
        """Clear the rating system."""

    def top(self) -> AsyncIterator[Rating]:
        """Get the top games."""

    async def score(self, game: str) -> float:
        """Get the score of a game.

        Args:
            game (str): The game to get the score of
        """


class EloRatingSystem:
    def __init__(self, reports: ReportStore, k: float = 32, initial: float = 1000):
        self.k = k
        self.initial = initial
        self.ratings: dict[str, float] = {}
        self.runs: dict[str, int] = {}
        self.planned_pairs: set[frozenset[str]] = set()
        self.reports: ReportStore = reports

    async def clear(self) -> None:
        self.ratings.clear()
        self.runs.clear()

    async def register(self, preference: Preference) -> None:
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

    async def top(self) -> AsyncIterator[Rating]:
        for item in sorted(
            (Rating(game, score) for game, score in self.ratings.items()),
            key=operator.attrgetter("score"),
            reverse=True,
        ):
            yield item

    async def score(self, game: str) -> float:
        return self.ratings.get(game, self.initial)

    async def launch(
        self, launcher: Launcher, capacity: int, owner: str
    ) -> list[GameMeta]:
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

    async def launch_preloaded(
        self, launcher: Prelauncher, capacity: int, owner: str
    ) -> list[GameMeta]:
        available = [game for game in launcher.games if game.email != owner]
        for game in available:
            if owner in [
                report.author for report in await self.reports.get(game.folder_name)
            ]:
                available.remove(game)
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

    async def report(self, game: GameMeta, report: Report) -> int | None:
        if game.email == report.author:
            return None
        return await self.reports.append(game.folder_name, report)


class RAMPreferenceStore:
    def __init__(self):
        self.preferences: dict[uuid.UUID, Preference] = {}
        self.rating_systems: list[RatingSystem] = []

    async def get(self, key: uuid.UUID) -> Preference | None:
        return self.preferences[key]

    async def set(self, key: uuid.UUID, value: Preference) -> None:
        preference_exists = key in self.preferences
        self.preferences[key] = value
        if preference_exists:
            await self.rebuild()
        else:
            for rating_system in self.rating_systems:
                await rating_system.register(value)

    async def delete(self, key: uuid.UUID) -> None:
        del self.preferences[key]
        await self.rebuild()

    async def __aiter__(self) -> AsyncIterator[Preference]:
        for value in self.preferences.values():
            yield value

    async def bind(self, rating_system: RatingSystem) -> None:
        async for preference in self:
            await rating_system.register(preference)
        self.rating_systems.append(rating_system)

    async def rebuild(self) -> None:
        for rating_system in self.rating_systems:
            await rating_system.clear()
            async for preference in self:
                await rating_system.register(preference)
