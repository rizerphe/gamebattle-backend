"""The caller's game.

Each owner has one game. Its id is the player id used on sessions. The name is
the label shown for that game; it is unset until the owner chooses one.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Game:
    id: UUID
    name: str | None
