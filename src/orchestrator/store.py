"""Players and their games.

Each player has one ``GameFiles`` history, stored in MongoDB. Launching a
snapshot is ``Sessions``' job; this only resolves players, games, and snapshots.
"""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from pymongo.asynchronous.database import AsyncDatabase

from orchestrator.files.models import GameFiles, NoEntrypoint, Snapshot, entrypoint
from orchestrator.files.mongo import MongoGameFiles
from orchestrator.games.models import Game


class Missing(Exception):
    pass


@dataclass
class Player:
    id: UUID
    files: GameFiles


class Store:
    def __init__(self, db: AsyncDatabase):
        self.db = db

    def player(self, key: str) -> Player:
        player_id = uuid5(NAMESPACE_URL, f"gamebattle:{key}")
        return Player(id=player_id, files=MongoGameFiles(self.db, player_id))

    async def own_game(self, owner: str) -> Game:
        player = self.player(owner)
        document = await self.db.players.find_one({"_id": player.id})
        name = document.get("name") if document else None
        return Game(id=player.id, name=name if isinstance(name, str) else None)

    async def game(self, game_id: UUID) -> Game:
        """Anyone's game. Unlike ``own_game``, an id nobody has used is ``Missing``."""

        document = await self.db.players.find_one({"_id": game_id})
        if document is None and not await self.db.snapshots.find_one({"player": game_id}):
            raise Missing(game_id)
        name = document.get("name") if document else None
        return Game(id=game_id, name=name if isinstance(name, str) else None)

    async def list_games(self) -> list[Game]:
        """Games that have a stored name or a snapshot.

        ``GET /game`` alone does not create either, so a caller who has only
        been identified is absent. Names sort first, case-insensitively, then
        id; an unset name sorts last.
        """

        names: dict[UUID, str | None] = {}
        for document in await self.db.players.find().to_list():
            name = document.get("name")
            names[document["_id"]] = name if isinstance(name, str) else None
        for player_id in await self.db.snapshots.distinct("player"):
            names.setdefault(player_id, None)
        games = [Game(id=game_id, name=name) for game_id, name in names.items()]
        return sorted(
            games,
            key=lambda game: (game.name is None, (game.name or "").casefold(), game.id),
        )

    async def game_files(self, game_id: UUID) -> GameFiles:
        """Anyone's files. An id nobody has used is ``Missing``."""

        await self.game(game_id)
        return MongoGameFiles(self.db, game_id)

    async def revision(self, game_id: UUID, snapshot_id: UUID) -> Snapshot:
        """One snapshot of ``game_id``."""

        try:
            return await MongoGameFiles(self.db, game_id).get_snapshot(snapshot_id)
        except KeyError as error:
            raise Missing(snapshot_id) from error

    async def published(self, game_id: UUID) -> Snapshot:
        """The snapshot ``game_id`` currently publishes."""

        try:
            return await MongoGameFiles(self.db, game_id).get_snapshot("published")
        except KeyError as error:
            raise Missing(game_id) from error

    async def set_game_name(self, owner: str, name: str) -> Game:
        player = self.player(owner)
        await self.db.players.update_one(
            {"_id": player.id}, {"$set": {"name": name}}, upsert=True
        )
        return Game(id=player.id, name=name)

    async def published_games(self) -> list[tuple[UUID, Snapshot]]:
        """Published snapshots that have an entrypoint, in player-id order."""

        documents = await self.db.players.find(
            {"published": {"$exists": True, "$ne": None}}
        ).to_list()
        found = []
        for document in documents:
            game_id = document["_id"]
            try:
                snapshot = await self.published(game_id)
                entrypoint(snapshot)
            except (Missing, NoEntrypoint):
                continue
            found.append((game_id, snapshot))
        return sorted(found, key=lambda item: item[0])
