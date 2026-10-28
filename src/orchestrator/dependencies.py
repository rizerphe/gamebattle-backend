from typing import Annotated

from fastapi import Depends, Request

from orchestrator.auth import Owner
from orchestrator.comparisons.service import Comparisons
from orchestrator.reports.credibility import ReportAssessor
from orchestrator.sessions.service import Sessions
from orchestrator.store import Player, Store
from orchestrator.webhooks.notify import Notifier


def get_store(request: Request) -> Store:
    return request.app.state.store


StoreDep = Annotated[Store, Depends(get_store)]


def get_sessions(request: Request) -> Sessions:
    return request.app.state.sessions


SessionsDep = Annotated[Sessions, Depends(get_sessions)]


def get_comparisons(request: Request) -> Comparisons:
    return request.app.state.comparisons


ComparisonsDep = Annotated[Comparisons, Depends(get_comparisons)]


def get_notifier(request: Request) -> Notifier:
    return request.app.state.notifier


NotifierDep = Annotated[Notifier, Depends(get_notifier)]


def get_assessor(request: Request) -> ReportAssessor | None:
    return request.app.state.assessor


AssessorDep = Annotated[ReportAssessor | None, Depends(get_assessor)]


def get_player(store: StoreDep, owner: Owner) -> Player:
    return store.player(owner)


PlayerDep = Annotated[Player, Depends(get_player)]
