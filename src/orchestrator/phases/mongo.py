"""The phase schedule in MongoDB: one document, a start time per phase."""

from pymongo.asynchronous.database import AsyncDatabase

from orchestrator.phases.models import SCHEDULED, Phase, Schedule

_ID = "schedule"


class MongoPhases:
    def __init__(self, db: AsyncDatabase) -> None:
        self._phases = db.phases

    async def schedule(self) -> Schedule:
        doc = await self._phases.find_one({"_id": _ID}) or {}
        return Schedule(**{phase.value: doc.get(phase.value) for phase in SCHEDULED})

    async def set_start(self, phase: Phase, at: float | None) -> None:
        update = {"$unset": {phase.value: ""}} if at is None else {"$set": {phase.value: at}}
        await self._phases.update_one({"_id": _ID}, update, upsert=True)
