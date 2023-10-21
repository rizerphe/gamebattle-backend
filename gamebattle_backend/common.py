"""Common types."""
from enum import Enum

from pydantic.dataclasses import dataclass


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
        formatted_name = self.folder_name.lower().replace(" ", "-")
        return f"gamebattle-{formatted_name}"

    @classmethod
    def folder_name_for(cls, email: str) -> str:
        """The name of the game's folder"""
        return email.split("@")[0].split(".")[0]

    @property
    def folder_name(self) -> str:
        """The name of the game's folder"""
        return self.folder_name_for(self.email)
