"""Redis preference store implementation."""
import json
from typing import AsyncIterable
import uuid

import redis.asyncio as redis

from gamebattle_backend.preferences import Preference, RatingSystem


class RedisPreferenceStore:
    """Redis preference store implementation."""

    def __init__(self, client: redis.Redis) -> None:
        """Initialize the Redis preference store.

        Args:
            client: Redis client.
        """
        self.client = client
        self.rating_systems: list[RatingSystem] = []

    async def get(self, key: uuid.UUID) -> Preference | None:
        """Get a preference.

        Args:
            key (str): The session id
        """
        preference_data = await self.client.hmget(
            f"preference:{key}", ["games", "score", "author", "timestamp"]
        )
        if not preference_data[0]:
            return None
        if preference_data[1] is None:
            return None
        if preference_data[2] is None:
            return None
        if preference_data[3] is None:
            return None
        try:
            return Preference(
                games=json.loads(preference_data[0]),
                first_score=json.loads(preference_data[1]),
                author=preference_data[2].decode("utf-8", errors="ignore"),
                timestamp=json.loads(preference_data[3]),
            )
        except json.JSONDecodeError:
            return None

    async def set(self, key: uuid.UUID, value: Preference) -> None:
        """Set a preference.

        Args:
            key (str): The session id
            value (Preference): The preference
        """
        preference_exists = await self.client.exists(f"preference:{key}")
        await self.client.hmset(
            f"preference:{key}",
            {
                "games": json.dumps(value.games),
                "score": json.dumps(value.first_score),
                "author": value.author,
                "timestamp": json.dumps(value.timestamp),
            },
        )
        if preference_exists:
            preferences = await self.sorted_preferences()
            for rating_system in self.rating_systems:
                await self.build_system(preferences, rating_system)
        else:
            for rating_system in self.rating_systems:
                await rating_system.register(value)

    async def delete(self, key: uuid.UUID) -> None:
        """Delete a preference.

        Args:
            key (str): The session id
        """
        await self.client.delete(f"preference:{key}")
        sorted_preferences = await self.sorted_preferences()
        for rating_system in self.rating_systems:
            await self.build_system(sorted_preferences, rating_system)

    async def accumulation_of_preferences_by(
        self, preference_author_email: str
    ) -> float:
        """Get the accumulation of preferences by a user.

        Args:
            preference_author_email (str): The email of the preference author
        """
        n_preferences: float = 0
        async for preference in self.get_all_preferences():
            if preference.author == preference_author_email:
                n_preferences += preference.accummulation
        return n_preferences

    async def bind(self, rating_system: RatingSystem) -> None:
        """Bind a rating system.

        Args:
            rating_system (RatingSystem): The rating system to bind
        """
        self.rating_systems.append(rating_system)
        await self.build_system(await self.sorted_preferences(), rating_system)

    async def get_all_preferences(self) -> AsyncIterable[Preference]:
        async for key in self.client.scan_iter(match="preference:*"):
            preference = await self.get(
                uuid.UUID(key.decode("utf-8", errors="ignore").split(":")[1])
            )
            if preference is not None:
                yield preference

    async def sorted_preferences(self) -> list[Preference]:
        preferences: list[Preference] = []
        async for preference in self.get_all_preferences():
            preferences.append(preference)
        return sorted(preferences, key=lambda x: x.timestamp)

    async def build_system(
        self, preferences: list[Preference], rating_system: RatingSystem
    ) -> None:
        await rating_system.clear()
        for preference in preferences:
            await rating_system.register(preference)
