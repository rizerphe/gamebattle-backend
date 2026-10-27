"""Attach recording length to the session embedded in each report."""

from dataclasses import replace

from orchestrator.reports.models import Report
from orchestrator.sessions.recording import enrich_sessions
from orchestrator.sessions.service import Sessions


async def enrich_report(sessions: Sessions, report: Report) -> Report:
    enriched = await enrich_sessions(sessions.terminals, [report.session])
    return replace(report, session=enriched[0])


async def enrich_reports(sessions: Sessions, reports: list[Report]) -> list[Report]:
    if not reports:
        return []
    enriched = {
        session.id: session
        for session in await enrich_sessions(
            sessions.terminals,
            [report.session for report in reports],
        )
    }
    return [replace(report, session=enriched[report.session.id]) for report in reports]
