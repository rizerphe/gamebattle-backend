"""The caller, as the homepage sees them."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter

from orchestrator.auth import CallerDep
from orchestrator.dependencies import StoreDep

router = APIRouter()


@dataclass(frozen=True)
class PlayerInfo:
    id: UUID
    admin: bool


@router.get("/player")
async def read_player(store: StoreDep, caller: CallerDep) -> PlayerInfo:
    player = store.player(caller.owner)
    await _link_account(store, player.id, caller.owner)
    return PlayerInfo(id=player.id, admin=caller.admin)


async def _link_account(store: StoreDep, player_id: UUID, uid: str) -> None:
    """Remember which Firebase account this player is, so admins can see a name.

    A player who has not stored anything yet gets no record, so that being
    signed in alone does not add a game to the list.
    """

    has_record = await store.db.players.find_one({"_id": player_id}, {"_id": 1})
    if has_record is None and not await store.db.snapshots.find_one({"player": player_id}):
        return
    await store.db.players.update_one(
        {"_id": player_id, "firebase_uid": {"$ne": uid}},
        {"$set": {"firebase_uid": uid}},
        upsert=has_record is None,
    )
