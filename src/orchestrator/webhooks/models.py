"""Webhooks admins register to hear about reports and removed games.

A webhook is a URL that accepts a Discord-style JSON body, sent for one event:
every report on a game's published version, the first one, the report count reaching
``threshold``, or the game being taken down automatically by reports.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

Event = Literal["every_report", "first_report", "threshold", "removed"]


@dataclass(frozen=True)
class Webhook:
    id: UUID
    url: str
    event: Event
    threshold: int | None
    created_at: float
