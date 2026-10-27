"""``GET /summary``: one sentence about the caller's game for the home page.

It is a JSON string. Without a game, a main file, or a working LLM, the
placeholder is returned instead.
"""

from fastapi import APIRouter, Request

from orchestrator.auth import Owner
from orchestrator.dependencies import StoreDep
from orchestrator.files.models import NoEntrypoint, entrypoint

router = APIRouter()

_PLACEHOLDER = "Get started by creating a game"


@router.get("/summary")
async def read_summary(request: Request, store: StoreDep, owner: Owner) -> str:
    player = store.player(owner)
    snapshot = await player.files.get_snapshot("current")
    try:
        path = entrypoint(snapshot)
    except NoEntrypoint:
        return _PLACEHOLDER
    code = next(file.saved_content for file in snapshot.files if file.path == path)
    line = await request.app.state.summaries.line(
        player.id, (code or b"").decode(errors="replace")
    )
    return line or _PLACEHOLDER
