"""Common types."""

from enum import Enum

import yaml
from email_normalize import Normalizer
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


@dataclass
class Team:
    id: str
    name: str
    member_emails: list[str]


class TeamManager:
    """Manage teams."""

    def __init__(self):
        self.teams = {}
        self.normalizer = Normalizer()

    async def from_yaml(self, file: str):
        """Load teams from a yaml file."""
        with open(file, "r") as f:
            data = yaml.safe_load(f)
            for team_id, team_data in data.items():
                self.teams[team_id] = Team(
                    id=team_id,
                    name=team_data["name"],
                    member_emails=[
                        (await self.normalizer.normalize(email)).normalized_address
                        for email in team_data["members"]
                    ],
                )
                print(f"Loaded team {team_id}: {self.teams[team_id]}", flush=True)

    async def team_of(self, email: str) -> Team | None:
        """Get the team of an email."""
        email = (await self.normalizer.normalize(email)).normalized_address
        for team in self.teams.values():
            if email in team.member_emails:
                return team
        return None

    def __getitem__(self, key: str) -> Team:
        """Get a team by name."""
        return self.teams[key]

    def get(self, key: str) -> Team | None:
        """Get a team by name."""
        return self.teams.get(key)


@dataclass(frozen=True)
class GameMeta:
    """The metadata of a game."""

    name: str
    team_id: str
    file: str

    @property
    def image_name(self) -> str:
        """The name of the image."""
        return f"gamebattle-{self.team_id}"

    def allowed_access(self, email: str, team_manager: TeamManager) -> bool:
        """Check if the email is allowed to access the game."""
        team = team_manager.get(self.team_id)
        if team is None:
            return False
        return email in team.member_emails
