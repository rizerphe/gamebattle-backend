"""A store for game preferences"""

from __future__ import annotations

import operator
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from gamebattle_backend.common import GameMeta
from gamebattle_backend.launcher import Launcher

from .report import Report
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
                session.games[0].metadata.team_id,
                session.games[1].metadata.team_id,
            ),
            first_score,
            session.owner,
        )

    @property
    def accummulation(self) -> float:
        return 1


@dataclass
class Rating:
    """A rating for a game"""

    name: str
    score: float


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

    async def accumulation_of_preferences_by(
        self, preference_author_email: str
    ) -> float:
        """Get the accumulation of preferences by a user.

        Args:
            preference_author_email (str): The email of the preference author
        """

    async def bind(self, rating_system: RatingSystem) -> None:
        """Bind a rating system.

        Args:
            rating_system (RatingSystem): The rating system to bind
        """

    async def sorted_preferences(self) -> list[Preference]:
        """Get all preferences sorted by timestamp.

        Returns:
            list[Preference]: List of preferences sorted by timestamp
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

    async def exclude(self, team_id: str, /) -> None:
        """Exclude a game from competition.

        Args:
            team_id (str): The team ID of the game to exclude
        """

    async def include(self, team_id: str, /) -> None:
        """Re-include a game in competition.

        Args:
            team_id (str): The team ID of the game to include
        """

    async def is_excluded(self, team_id: str, /) -> bool:
        """Check if a game is excluded from competition.

        Args:
            team_id (str): The team ID of the game to check

        Returns:
            bool: True if the game is excluded
        """

    async def excluded_games(self) -> set[str]:
        """Get all excluded game team IDs.

        Returns:
            set[str]: Set of excluded team IDs
        """


class RatingSystem(Protocol):
    """A rating system for games"""

    async def register(self, preference: Preference) -> None:
        """Register a preference.

        Args:
            preference (Preference): The preference to register
        """
        ...

    async def clear(self) -> None:
        """Clear the rating system."""
        ...

    def top(self, launcher: Launcher) -> AsyncIterator[Rating]:
        """Get the top games."""
        ...

    async def score(self, game: str) -> float:
        """Get the score of a game.

        Args:
            game (str): The game to get the score of
        """
        ...


class EloRatingSystem:
    def __init__(self, reports: ReportStore, k: float = 32, initial: float = 1000):
        self.k = k
        self.initial = initial
        self.ratings: dict[str, float] = {}
        self.runs: dict[str, int] = {}
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

        min_score = min(self.ratings.values())
        if min_score < 0:
            for game in self.ratings:
                self.ratings[game] -= min_score

    def expected(self, preference: Preference) -> tuple[float, float]:
        return (
            self.expected_score(preference.games[0], preference.games[1]),
            self.expected_score(preference.games[1], preference.games[0]),
        )

    def expected_score(self, game: str, other: str) -> float:
        return 1 / (1 + 10 ** ((self.ratings[other] - self.ratings[game]) / 400))

    async def top(self, launcher: Launcher) -> AsyncIterator[Rating]:
        excluded = await self.reports.excluded_games()
        for item in sorted(
            (
                Rating(launcher[game].name, score)
                for game, score in self.ratings.items()
                if game in launcher and game not in excluded
            ),
            key=operator.attrgetter("score"),
            reverse=True,
        ):
            yield item

    async def score(self, game: str) -> float:
        return self.ratings.get(game, self.initial)

    async def score_if_exists(self, game: str) -> float | None:
        return self.ratings.get(game)

    async def score_and_played(self, game: str) -> tuple[float, int]:
        return self.ratings.get(game, self.initial), self.runs.get(game, 0)

    async def score_and_played_if_exists(self, game: str) -> tuple[float | None, int]:
        return self.ratings.get(game), self.runs.get(game, 0)

    async def launch(
        self,
        launcher: Launcher,
        capacity: int,
        owner: str,
        avoid: frozenset[str] = frozenset(),
    ) -> list[GameMeta]:
        excluded = await self.reports.excluded_games()
        available = [
            game
            for game in launcher.games
            if not await launcher.allowed_access(game, owner)
            and game.team_id not in avoid
            and game.team_id not in excluded
        ]
        for game in available:
            if owner in [
                report.author for report in await self.reports.get(game.team_id)
            ]:
                available.remove(game)
        game_pairs = [
            (game, other) for game in available for other in available if game != other
        ]
        if not game_pairs:
            return []
        game_pairs.sort(
            key=lambda pair: self.pair_likelihood(pair[0].team_id, pair[1].team_id),
            reverse=True,
        )
        return ([game for pair in game_pairs for game in pair] + launcher.games)[
            :capacity
        ]

    def pair_likelihood(self, game: str, other: str) -> float:
        return abs(
            self.ratings.get(game, self.initial) - self.ratings.get(other, self.initial)
        ) / 200 - (self.runs.get(game, 0) + self.runs.get(other, 0))

    async def report(self, game: GameMeta, report: Report) -> int | None:
        return await self.reports.append(game.team_id, report)

    async def fetch_reports(self, game: str) -> tuple[Report, ...]:
        return await self.reports.get(game)

    def replay_with_history(
        self, preferences: list[Preference]
    ) -> list[tuple[Preference, dict[str, tuple[float, float]]]]:
        """Replay preferences and return ELO changes for each.

        Args:
            preferences: List of preferences in chronological order.

        Returns:
            List of (preference, elo_changes) tuples where elo_changes maps
            team_id to (before, after) ELO values.
        """
        ratings: dict[str, float] = {}
        result: list[tuple[Preference, dict[str, tuple[float, float]]]] = []

        for preference in preferences:
            # Initialize ratings for new games
            for game in preference.games:
                if game not in ratings:
                    ratings[game] = self.initial

            # Capture before values
            before_values = {game: ratings[game] for game in preference.games}

            # Calculate expected scores
            expected_first = 1 / (
                1
                + 10
                ** (
                    (ratings[preference.games[1]] - ratings[preference.games[0]]) / 400
                )
            )
            expected_second = 1 - expected_first

            # Update ratings
            ratings[preference.games[0]] += self.k * (
                preference.first_score - expected_first
            )
            ratings[preference.games[1]] += self.k * (
                (1 - preference.first_score) - expected_second
            )

            # Normalize if any rating went negative
            min_score = min(ratings.values())
            if min_score < 0:
                for game in ratings:
                    ratings[game] -= min_score

            # Capture after values
            elo_changes = {
                game: (before_values[game], ratings[game])
                for game in preference.games
            }
            result.append((preference, elo_changes))

        return result


class RAMPreferenceStore:
    def __init__(self) -> None:
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

    async def sorted_preferences(self) -> list[Preference]:
        return sorted(self.preferences.values(), key=lambda x: x.timestamp)

    async def rebuild(self) -> None:
        for rating_system in self.rating_systems:
            await rating_system.clear()
            async for preference in self:
                await rating_system.register(preference)
