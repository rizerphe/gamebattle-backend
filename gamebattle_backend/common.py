"""Common types."""
from enum import Enum

from pydantic.dataclasses import dataclass


@dataclass
class GameOutput:
    """The output of a game.

    Attributes:
        output (str): The characters appended to the output since the last request
        whole (str): All the output
        done (bool): Whether the game has finished
    """

    output: str
    whole: str
    done: bool


class RequestStatus(Enum):
    """The status of a request."""

    OK = "ok"
    ERROR = "error"


@dataclass
class Status:
    """A status of either "ok" or "error".

    Attributes:
        status (str): The status
    """

    status: RequestStatus


@dataclass(frozen=True)
class GameMeta:
    """The metadata of a game."""

    name: str
    author: str
    file: str
    email: str

    @property
    def container_name(self) -> str:
        """The name of the container."""
        formatted_name = self.name.lower().replace(" ", "-")
        return f"gamebattle-{formatted_name}"
