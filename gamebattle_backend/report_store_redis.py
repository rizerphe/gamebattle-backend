"""Redis report store."""

import json
import uuid

import redis.asyncio as redis

from gamebattle_backend.preferences import Report


class RedisReportStore:
    """Redis report store implementation."""

    def __init__(self, client: redis.Redis) -> None:
        """Initialize the Redis report store.

        Args:
            client: Redis client.
        """
        self.client = client

    async def get(self, key: str) -> tuple[Report, ...]:
        """Get a report.

        Args:
            key (str): The game name
        """
        report_data = await self.client.lrange(f"report:{key}", 0, -1)
        return tuple(
            Report(
                session=uuid.UUID(report["session"]),
                short_reason=report.get("short_reason", "other"),
                reason=report.get("reason", ""),
                output=report.get("output", ""),
                author=report.get("author", "unknown"),
            )
            for report in map(json.loads, report_data)
        )

    async def append(self, key: str, value: Report) -> int:
        """Append a report.

        Args:
            key (str): The game name
            value (Report): The report

        Returns:
            int: The length of the list after the push operation.
        """
        return await self.client.rpush(
            f"report:{key}",
            json.dumps(
                {
                    "session": value.session.hex,
                    "short_reason": value.short_reason,
                    "reason": value.reason,
                    "output": value.output,
                    "author": value.author,
                }
            ),
        )

    async def delete(self, key: str) -> None:
        """Delete all reports.

        Args:
            key (str): The game name
        """
        await self.client.delete(f"report:{key}")
