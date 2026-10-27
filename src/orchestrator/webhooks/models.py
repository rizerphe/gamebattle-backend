"""Webhooks admins register to hear about reports and removed games.

A webhook is a URL that accepts a Discord-style JSON body. It fires when a
game's report count on its current publication reaches one of ``thresholds``,
and, if ``on_removed`` is set, when a game stops being published.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Webhook:
    id: UUID
    url: str
    thresholds: tuple[int, ...]
    on_removed: bool
    created_at: float
