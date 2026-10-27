"""Webhooks admins register to hear about reports and removed games.

A webhook is a URL that accepts a Discord-style JSON body. Each of its three
triggers is independent: the first report on a game's published version, the
report count reaching ``threshold`` (unset when off), and the game being taken
down automatically by reports.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Webhook:
    id: UUID
    url: str
    on_first_report: bool
    threshold: int | None
    on_removed: bool
    created_at: float
