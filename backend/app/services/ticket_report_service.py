"""IT ticket analysis reports: upload a Freshworks export, analyze it, review the report, send it to the IT team.

The lifecycle (one `TicketReport` row):

    UPLOADED --analyze--> ANALYZING --> COMPLETED          email: NOT_SENT --send--> SENDING --> SENT
                                    \\-> FAILED (analyze again)                              \\-> FAILED (send again)

- **Nothing is automatic.** Analyzing needs a click, and so does sending; the analysis never emails anyone.
- **Once only.** Starting an analysis and starting a send are conditional UPDATEs on the row's status, so a repeated
  click, a retried request, or two browser tabs cannot run the same analysis or send the same report twice.
- **Sending never calls the model.** The email is built from the analysis saved on the row.
- **Failures are explicit.** A failed analysis or a failed send is recorded as such, with a message that carries no
  ticket content, and can be retried. "Sent" is only recorded after the mail service confirms.
- **Nothing is written back** to Freshworks: the export is read, the report is advice.
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import async_session_factory
from app.db.models import TicketReport
from app.llm.factory import get_provider
from app.notifications.factory import get_email_provider
from app.services import ticket_report_ai as ai
from app.services import ticket_report_email as report_email
from app.services import ticket_report_metrics as metrics
from app.services.ticket_report_parser import ACCEPTED_EXTENSIONS, ParsedUpload, parse_upload

logger = logging.getLogger("hfmg.reports")

settings = get_settings()

UPLOADED, ANALYZING, COMPLETED, FAILED = "UPLOADED", "ANALYZING", "COMPLETED", "FAILED"
NOT_SENT, SENDING, SENT = "NOT_SENT", "SENDING", "SENT"
MAX_CONCURRENT_ANALYSES = 2
#: The layout of a saved analysis. A report saved with another one is not rendered or emailed (upload the file again).
ANALYSIS_VERSION = 2
OLD_LAYOUT_MESSAGE = "This report was made with an older report layout. Upload the file again to analyze it."
MAX_EMAIL_LOG = 20
SEND_FAILED_MESSAGE = "The mail service did not accept the report. Nothing was sent; you can try again."
ANALYSIS_FAILED_MESSAGE = "The analysis could not be completed. Nothing was sent. You can run it again."


class ReportError(Exception):
    """A request the lifecycle cannot honour. `status_code` is the HTTP status the API answers with."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def feature_enabled() -> bool:
    return settings.enable_ticket_reports


# --- configuration the dashboard needs ------------------------------------------------------------------------------


MAX_RECIPIENTS = 10  # a distribution list is one address; a few test addresses are fine; a long list is a mistake


def configured_recipient() -> tuple[list[str] | None, str | None]:
    """(the addresses the report goes to, or None) and (what is wrong with the configuration, or None).

    IT_REPORT_DISTRIBUTION_EMAIL is normally the one address of the IT team's distribution list. Several addresses
    separated by commas or semicolons are accepted (for testing); each must be valid, and none is added twice."""
    raw = settings.it_report_distribution_email.strip()
    if not raw:
        return None, "IT_REPORT_DISTRIBUTION_EMAIL is not set, so there is nowhere to send the report."
    addresses: list[str] = []
    for part in re.split(r"[,;]", raw):
        part = part.strip()
        if not part:
            continue
        try:
            address = validate_email(part, check_deliverability=False).normalized
        except EmailNotValidError:
            return None, "IT_REPORT_DISTRIBUTION_EMAIL contains an address that is not valid."
        if address.lower() not in (a.lower() for a in addresses):
            addresses.append(address)
    if not addresses:
        return None, "IT_REPORT_DISTRIBUTION_EMAIL is not set, so there is nowhere to send the report."
    if len(addresses) > MAX_RECIPIENTS or len(", ".join(addresses)) > 300:
        return None, f"IT_REPORT_DISTRIBUTION_EMAIL lists too many addresses (at most {MAX_RECIPIENTS})."
    return addresses, None


def email_problem() -> tuple[list[str] | None, str | None]:
    """(recipients, problem): a problem if the recipients or the mail provider is not usable."""
    recipients, problem = configured_recipient()
    if problem:
        return None, problem
    if not get_email_provider().is_configured:
        return recipients, "Email sending is not configured (EMAIL_PROVIDER and its credentials)."
    return recipients, None


def public_config() -> dict[str, Any]:
    enabled = feature_enabled()
    recipients, problem = email_problem() if enabled else (None, None)
    recipient = ", ".join(recipients) if recipients else None
    return {
        "enabled": enabled,
        "accepted_extensions": list(ACCEPTED_EXTENSIONS),
        "max_upload_bytes": settings.ticket_report_max_upload_bytes,
        "max_rows": settings.ticket_report_max_rows,
        "ai_configured": bool(enabled and get_provider().is_configured),
        "email": {"configured": enabled and problem is None, "recipient": recipient, "problem": problem},
    }


# --- upload ---------------------------------------------------------------------------------------------------------


def _safe_filename(name: str | None) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or "upload")[:255]


async def purge_expired(db: AsyncSession) -> int:
    cutoff = _now() - timedelta(days=settings.ticket_report_retention_days)
    result = await db.execute(delete(TicketReport).where(TicketReport.created_at < cutoff))
    await db.commit()
    return result.rowcount or 0


def validation_payload(parsed: ParsedUpload) -> dict[str, Any]:
    return {
        "valid": parsed.valid,
        "errors": parsed.errors,
        "warnings": parsed.warnings,
        "fields_present": parsed.fields_present,
    }


async def create_upload(db: AsyncSession, filename: str | None, content: bytes) -> TicketReport:
    """Validate and store an upload. Raises `ReportUploadError` for a file that cannot be used at all; a file that
    can be read but cannot be analyzed (no ticket-ID column, ...) is stored with its validation errors."""
    parsed = parse_upload(filename or "", content)
    await purge_expired(db)
    report = TicketReport(
        filename=_safe_filename(filename),
        file_type=parsed.file_type,
        source_row_count=parsed.source_row_count,
        row_count=len(parsed.rows),
        detected_columns=parsed.columns,
        validation=validation_payload(parsed),
        dataset=parsed.rows if parsed.valid else None,
        status=UPLOADED,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    logger.info("Report %s uploaded: %d rows, valid=%s", report.id, report.row_count, parsed.valid)
    return report


# --- reading a report -----------------------------------------------------------------------------------------------


async def get_report(db: AsyncSession, report_id) -> TicketReport | None:
    """The report, with an analysis that stopped being worked on (the server restarted) turned into a failure."""
    report = await db.get(TicketReport, report_id)
    if report is None:
        return None
    if report.status == ANALYZING and report.analysis_started_at is not None:
        age = (_now() - report.analysis_started_at).total_seconds()
        if age > settings.ticket_report_analysis_timeout_seconds:
            await db.execute(
                update(TicketReport)
                .where(TicketReport.id == report.id, TicketReport.status == ANALYZING)
                .values(status=FAILED, progress=None, error="The analysis was interrupted. Run it again.")
            )
            await db.commit()
            await db.refresh(report)
    return report


def can_send(report: TicketReport) -> tuple[bool, str | None]:
    """Whether the Send button may be offered, and if not, why (shown as the reason it is disabled)."""
    if report.status != COMPLETED or not report.analysis:
        return False, "The report has not been analyzed yet."
    if report.analysis.get("version") != ANALYSIS_VERSION:
        return False, OLD_LAYOUT_MESSAGE
    if report.email_status == SENT:
        return False, "This report has already been sent."
    if report.email_status == SENDING and not _send_is_stale(report):
        return False, "The report is being sent."
    _, problem = email_problem()
    return (problem is None), problem


def _send_is_stale(report: TicketReport) -> bool:
    started = report.email_started_at
    return started is None or (_now() - started).total_seconds() > settings.ticket_report_send_stale_seconds


# --- analysis -------------------------------------------------------------------------------------------------------

_tasks: set[asyncio.Task] = set()


async def claim_analysis(db: AsyncSession, report_id) -> TicketReport:
    """Move UPLOADED/FAILED to ANALYZING, exactly once. Anything else is refused with the reason."""
    report = await get_report(db, report_id)
    if report is None:
        raise ReportError(404, "Report not found.")
    was_analyzing = report.status == ANALYZING  # read now: a rollback expires the row's attributes
    if report.status == COMPLETED:
        raise ReportError(409, "This report has already been analyzed.")
    if was_analyzing:
        raise ReportError(409, "This report is already being analyzed.")
    if not report.validation.get("valid") or not report.dataset:
        raise ReportError(422, "This file has validation errors, so it cannot be analyzed. Upload a corrected file.")
    cutoff = _now() - timedelta(seconds=settings.ticket_report_analysis_timeout_seconds)
    running = (await db.execute(
        select(func.count()).select_from(TicketReport).where(
            TicketReport.status == ANALYZING, TicketReport.analysis_started_at > cutoff
        )
    )).scalar_one()
    if running >= MAX_CONCURRENT_ANALYSES and not was_analyzing:
        raise ReportError(429, "Other analyses are still running. Try again in a minute.")
    claimed = await db.execute(
        update(TicketReport)
        .where(TicketReport.id == report.id, TicketReport.status.in_([UPLOADED, FAILED]))
        .values(
            status=ANALYZING, analysis_started_at=_now(), error=None,
            progress={"stage": "starting", "done": 0, "total": 0, "message": "Starting the analysis…"},
        )
        .returning(TicketReport.id)
    )
    if claimed.first() is None:
        # Another request got there first (a repeated click, or a second tab), or it is already done.
        await db.rollback()
        await db.refresh(report)
        if report.status == ANALYZING:
            raise ReportError(409, "This report is already being analyzed.")
        raise ReportError(409, "This report has already been analyzed.")
    await db.commit()
    await db.refresh(report)
    return report


def start_analysis(report_id) -> None:
    """Run the analysis in the background; the dashboard polls the report for progress."""
    task = asyncio.create_task(run_analysis(report_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_for_analyses() -> None:
    """Wait for background analyses to finish (tests, and an orderly shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _update(report_id, **values: Any) -> None:
    async with async_session_factory() as db:
        await db.execute(update(TicketReport).where(TicketReport.id == report_id).values(**values))
        await db.commit()


async def _fail(report_id, message: str) -> None:
    await _update(report_id, status=FAILED, progress=None, error=message[:500])


async def run_analysis(report_id) -> None:
    """Never raises: any failure is recorded on the report with a message that carries no ticket content."""
    try:
        async with async_session_factory() as db:
            report = await db.get(TicketReport, report_id)
            if report is None or not report.dataset:
                return
            rows, fields, validation = list(report.dataset), list(report.validation.get("fields_present") or []), report.validation
            filename, file_type, source_rows = report.filename, report.file_type, report.source_row_count

        provider = get_provider()
        analysis = await _analyze(report_id, rows, fields, validation, filename, file_type, source_rows, provider)
        if analysis is None:
            return
        await _update(
            report_id,
            status=COMPLETED, analysis=analysis, progress=None, error=None, dataset=None,  # the export is not kept
            analysis_completed_at=_now(),
        )
        logger.info("Report %s analyzed: %d tickets", report_id, len(rows))
    except Exception as exc:
        logger.exception("Report %s analysis failed unexpectedly: %s", report_id, type(exc).__name__)
        try:
            await _fail(report_id, ANALYSIS_FAILED_MESSAGE)
        except Exception:
            logger.exception("Report %s: could not record the failure", report_id)


async def _progress(report_id, stage: str, done: int, total: int, message: str) -> None:
    await _update(report_id, progress={"stage": stage, "done": done, "total": total, "message": message})


async def _analyze(report_id, rows, fields, validation, filename, file_type, source_rows, provider) -> dict[str, Any] | None:
    as_of = _now()
    enriched = metrics.enrich(rows, fields, as_of)
    summary = metrics.summarize(enriched, fields)
    unresolved = sorted((r for r in enriched if r["state"] != "resolved"), key=metrics.urgency_key)
    to_analyze = unresolved[: settings.ticket_report_max_ai_tickets]

    outcome = {"answers": {}, "batches": 0, "failed_batches": 0, "dropped": 0}
    if to_analyze:
        if not provider.is_configured:
            await _fail(report_id, "The AI provider is not configured (for OpenAI, set OPENAI_API_KEY), so the tickets cannot be analyzed.")
            return None
        batches = -(-len(to_analyze) // max(settings.ticket_report_batch_size, 1))
        await _progress(report_id, "analyzing", 0, batches, f"Analyzing {len(to_analyze)} tickets in {batches} step(s)…")

        async def on_batch(done: int, total: int) -> None:
            await _progress(report_id, "analyzing", done, total, f"Analyzed {done} of {total} steps…")

        outcome = await ai.analyze_tickets(to_analyze, on_batch)
        if not outcome["answers"]:
            await _fail(report_id, "The AI provider did not return usable results. Nothing was saved; try again.")
            return None

    tickets = metrics.sort_actions(_ticket_rows(unresolved, to_analyze, outcome["answers"]))
    analyzed = [t for t in tickets if t["ai"]["status"] == "analyzed"]
    followup = await _followup(report_id, tickets, to_analyze)

    employees = metrics.employee_table(tickets, fields)
    synthesis_state, synthesis = "skipped", None
    if len(analyzed) >= 2:
        await _progress(report_id, "summarizing", 0, 1, "Looking for recurring issues…")
        synthesis = await ai.synthesize(analyzed, employees)
        synthesis_state = "complete" if synthesis is not None else "unavailable"
    follow_ups = (synthesis or {}).get("employee_follow_ups", {})
    for entry in employees:
        entry["ai_follow_up"] = follow_ups.get(entry["name"], "")

    counts = {
        "analyzed": len(analyzed),
        "unavailable": sum(1 for t in tickets if t["ai"]["status"] == "unavailable"),
        "not_analyzed": sum(1 for t in tickets if t["ai"]["status"] == "not_analyzed"),
    }
    # What the AI recommended, counted from its validated answers. Not available when it analyzed nothing.
    summary["ai_analyzed"] = counts["analyzed"]
    summary["ai_critical"] = sum(1 for t in analyzed if t["ai"]["priority"] == "Critical") if analyzed else None
    summary["ai_high"] = sum(1 for t in analyzed if t["ai"]["priority"] == "High") if analyzed else None

    return {
        "version": ANALYSIS_VERSION,
        "as_of": as_of.isoformat(),
        "source": {"filename": filename, "file_type": file_type, "rows_in_file": source_rows, "rows_used": len(rows)},
        "summary_fields": fields,
        "summary": summary,
        "highlights": metrics.highlights(summary, employees, (synthesis or {}).get("focus")),
        "employees": employees,
        # In the order to work them (Critical to Low, overdue and oldest first). `action_ids` is the short list the
        # dashboard shows first and the email carries; the rest are one "show all" away.
        "tickets": tickets,
        "action_ids": metrics.headline_ids(tickets, settings.ticket_report_email_medium_items),
        "followup": followup,
        "recurring_issues": (synthesis or {}).get("recurring_issues", []),
        "recurring_note": _recurring_note(synthesis_state, synthesis, len(analyzed)),
        "ai": {
            "provider": getattr(provider, "name", "unknown"),
            "tickets_analyzed": counts["analyzed"],
            "tickets_unavailable": counts["unavailable"],
            "tickets_not_analyzed": counts["not_analyzed"],
            "batches": outcome["batches"],
            "failed_batches": outcome["failed_batches"],
            "dropped_answers": outcome["dropped"],
            "synthesis": synthesis_state,
        },
        "data_quality": _data_quality(validation, summary, fields, counts, outcome, synthesis_state, followup),
    }


async def _followup(report_id, tickets: list[dict[str, Any]], to_analyze: list[dict[str, Any]]) -> dict[str, Any]:
    """The weekly follow-up groups: waiting on a vendor, provider issues, and the priority issues affecting daily work.

    The vendor and provider groups come from a second AI pass that reads a longer excerpt of each description. If that
    pass fails, the groups are simply unavailable ("unavailable" / "partial"): the priorities and the rest of the
    report are unaffected. The daily-work group is the AI's existing Critical and High recommendations."""
    answers: dict[str, dict[str, Any]] = {}
    outcome = {"batches": 0, "failed_batches": 0, "dropped": 0}
    status = "skipped"
    if to_analyze:
        batches = -(-len(to_analyze) // max(settings.ticket_report_followup_batch_size, 1))
        await _progress(report_id, "followup", 0, batches, "Checking for vendor and provider issues…")

        async def on_batch(done: int, total: int) -> None:
            await _progress(report_id, "followup", done, total, f"Checked {done} of {total} steps for vendor and provider issues…")

        outcome = await ai.classify_followup(to_analyze, on_batch)
        answers = outcome["answers"]
        status = "unavailable" if not answers else "partial" if len(answers) < len(to_analyze) else "complete"
    return {
        **metrics.followup_sections(tickets, answers),
        "status": status,
        "classified": len(answers),
        "failed_batches": outcome["failed_batches"],
        "dropped_answers": outcome["dropped"],
    }


def _ticket_rows(unresolved, to_analyze, answers) -> list[dict[str, Any]]:
    """Open and unresolved tickets for the report. Resolved tickets are only counted, never stored."""
    limited = {r["ticket_id"] for r in to_analyze}
    out = []
    for row in unresolved:
        if row["ticket_id"] in answers:
            result = {"status": "analyzed", **answers[row["ticket_id"]]}
        elif row["ticket_id"] in limited:
            result = {"status": "unavailable"}
        else:
            result = {"status": "not_analyzed"}
        result.setdefault("priority", None)
        result.setdefault("reason", None)
        result.setdefault("next_action", None)
        result.setdefault("issue_label", None)
        result.setdefault("limited_information", False)
        out.append({
            "ticket_id": row["ticket_id"],
            "subject": row["subject"],
            "status": row["status"],
            "state": row["state"],
            "priority": row["priority"],
            "priority_level": row["priority_level"],
            "assignee": row["assignee"],
            "group": row["group"],
            "category": row["category"],
            "created": row["created"],
            "due": row["due"],
            "age_days": row["age_days"],
            "overdue": row["overdue"],
            "ai": result,
        })
    return out


def _recurring_note(state: str, synthesis: dict[str, Any] | None, analyzed: int) -> str:
    if state == "skipped":
        return "Not enough analyzed tickets to look for recurring issues."
    if state == "unavailable":
        return "The AI could not look for recurring issues this time; the rest of the report is unaffected."
    return "The AI found no issue that recurs across several of the analyzed tickets." if not (synthesis or {}).get("recurring_issues") else ""


_UNAVAILABLE = (
    ("open", "Open and resolved counts: the export has no Status column that could be read."),
    ("by_priority", "Priority counts and Critical/High totals: the export has no Priority column."),
    ("overdue", "Overdue tickets: this needs a Due by column and a readable Status."),
    ("sla_breaches", "SLA breaches: no ticket has a Resolution status, or both a Due by and a Resolved time to compare."),
    ("first_response_breaches", "First-response SLA: the export has no First Response Status with a value."),
    ("unassigned", "Unassigned tickets and employee workload: the export has no Assigned-to column."),
    ("top_categories", "Common categories: the export has no Category / Type column."),
)


def _data_quality(validation, summary, fields, counts, outcome, synthesis_state, followup) -> dict[str, list[str]]:
    unavailable = [text for key, text in _UNAVAILABLE if summary.get(key) is None]
    if summary["oldest_unresolved_age_days"] is None and summary["total"]:
        unavailable.append("Ticket age: the export has no readable Created time for unresolved tickets.")
    assumptions = [
        "Open and resolved come from the Status column: Open, Pending, In progress, On hold, Waiting and similar are open; "
        "Resolved, Closed, Cancelled and similar are resolved. Any other status is not counted either way.",
        "A ticket is overdue only when it is open and its Due by time has passed. An old ticket is not assumed to be overdue.",
        "An SLA breach is a ticket the export's SLA column marks as breached, or one resolved after its Due by time.",
        "Priority totals use the Freshworks priority text (Urgent/Critical, High, Medium, Low); other values are listed as unrecognized.",
        "Dates without a time zone are read as UTC, and ages are counted to the time of the analysis.",
        "Unassigned means not resolved and with no agent in the export.",
    ]
    limitations = [
        "The AI saw each ticket's subject and a shortened description only, with emails, phone numbers, SSNs, dates of "
        "birth and record numbers masked. A patient's name written in free text cannot be recognized and may remain.",
        "AI priorities are recommendations for a person to review, not facts. Nothing is changed in Freshworks.",
        "Resolved and closed tickets are counted but not analyzed, so recurring issues cover open and unresolved tickets only.",
    ]
    if summary["unknown_status"]:
        limitations.append(
            f"{summary['unknown_status']} ticket(s) have a status the report could not read; they are counted as open "
            "or unresolved in the totals and the employee table."
        )
    if counts["not_analyzed"]:
        limitations.append(
            f"The Critical and High counts cover the {counts['analyzed']} tickets the AI analyzed; "
            f"{counts['not_analyzed']} open ticket(s) were beyond the limit of {settings.ticket_report_max_ai_tickets} "
            "sent to the AI (overdue and higher-priority tickets first) and have no recommendation."
        )
    if counts["unavailable"] or outcome["failed_batches"]:
        limitations.append(
            f"{counts['unavailable']} ticket(s) have no recommendation because the AI returned no usable answer for them."
        )
    if outcome["dropped"]:
        limitations.append(f"{outcome['dropped']} AI answer(s) were discarded because they did not match the tickets sent.")
    if synthesis_state == "unavailable":
        limitations.append("The recurring-issues step failed, so that section is empty.")
    if followup["status"] != "skipped":
        limitations.append(
            "For the weekly follow-up groups the AI read up to 2,200 characters of each description (its start and its end), "
            "with identifiers masked. Waiting on a vendor needs evidence in the ticket: a ticket that only hints at a vendor "
            "is listed as Needs Review. A ticket can appear in more than one group."
        )
    if followup["status"] == "unavailable":
        limitations.append("The weekly follow-up pass returned nothing usable, so the vendor and provider groups are empty.")
    elif followup["status"] == "partial":
        limitations.append(
            f"The weekly follow-up pass covered {followup['classified']} tickets; the rest have no vendor or provider classification."
        )
    return {
        "warnings": [w["message"] for w in validation.get("warnings", [])],
        "unavailable": unavailable,
        "assumptions": assumptions,
        "limitations": limitations,
    }


# --- sending --------------------------------------------------------------------------------------------------------


async def send_report(report_id) -> dict[str, Any]:
    """Send the saved report to the distribution list. Safe to call twice: the second call sends nothing.

    Runs shielded from request cancellation, so a closed browser tab cannot leave a send half-recorded.
    Returns {"report": TicketReport, "already_sent": bool}."""
    return await asyncio.shield(_send_report(report_id))


async def _send_report(report_id) -> dict[str, Any]:
    recipients, problem = email_problem()
    recipient = ", ".join(recipients) if recipients else None  # as recorded and shown; `recipients` is what is sent to
    async with async_session_factory() as db:
        report = await db.get(TicketReport, report_id)
        if report is None:
            raise ReportError(404, "Report not found.")
        if report.status != COMPLETED or not report.analysis:
            raise ReportError(409, "The report has not been analyzed yet, so there is nothing to send.")
        if report.analysis.get("version") != ANALYSIS_VERSION:
            raise ReportError(409, OLD_LAYOUT_MESSAGE)
        if report.email_status == SENT:
            return {"report": report, "already_sent": True}
        if problem or not recipients:
            raise ReportError(503, problem or "Email is not configured.")

        stale_cutoff = _now() - timedelta(seconds=settings.ticket_report_send_stale_seconds)
        claimed = await db.execute(
            update(TicketReport)
            .where(
                TicketReport.id == report.id,
                TicketReport.status == COMPLETED,
                or_(
                    TicketReport.email_status.in_([NOT_SENT, "FAILED"]),
                    and_(TicketReport.email_status == SENDING, TicketReport.email_started_at < stale_cutoff),
                ),
            )
            .values(
                email_status=SENDING, email_started_at=_now(), email_attempts=TicketReport.email_attempts + 1,
                email_recipient=recipient, email_error=None,
            )
            .returning(TicketReport.id)
        )
        if claimed.first() is None:
            await db.rollback()
            await db.refresh(report)
            if report.email_status == SENT:
                return {"report": report, "already_sent": True}
            raise ReportError(409, "This report is already being sent.")
        await db.commit()
        analysis = report.analysis

    sent = False
    try:
        provider = get_email_provider()
        sent = await provider.send(
            to=recipients,  # one email addressed to all of them, never one per address
            from_email=settings.email_from,
            subject=report_email.subject_line(analysis),
            body=report_email.build_text(analysis),
            html_body=report_email.build_html(analysis),
        )
    except Exception as exc:  # providers should not raise; if one does, the report was not sent
        logger.warning("Report %s: the mail provider raised %s", report_id, type(exc).__name__)
        sent = False

    async with async_session_factory() as db:
        report = await db.get(TicketReport, report_id)
        entry = {"at": _now().isoformat(), "outcome": "sent" if sent else "failed", "recipient": recipient}
        values: dict[str, Any] = {"email_log": [*(report.email_log or []), entry][-MAX_EMAIL_LOG:]}
        if sent:
            values.update(email_status=SENT, email_sent_at=_now(), email_error=None)
        else:
            values.update(email_status="FAILED", email_error=SEND_FAILED_MESSAGE)
        await db.execute(update(TicketReport).where(TicketReport.id == report_id).values(**values))
        await db.commit()
        await db.refresh(report)
    logger.info("Report %s email %s (attempt %d)", report_id, "sent" if sent else "failed", report.email_attempts)
    return {"report": report, "already_sent": False}
