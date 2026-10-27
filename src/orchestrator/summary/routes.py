"""A placeholder for the home-page sentence.

The UI types whatever string this returns. A real summary of the caller's
game replaces this later.
"""

from fastapi import APIRouter

from orchestrator.auth import Owner

router = APIRouter()

_PLACEHOLDER = "Get started by creating a game"


@router.get("/summary")
async def read_summary(_owner: Owner) -> str:
    return _PLACEHOLDER
