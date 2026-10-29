"""IT ticket analysis reports (see app/services/ticket_report_service.py).

    GET  /reports/config            what is on, the limits, who a report would be sent to
    POST /reports/upload            validate and store a Freshworks export (.csv / .xlsx)
    POST /reports/{id}/analyze      start the AI analysis (202; poll the report)
    GET  /reports/{id}              the report: validation, progress, analysis, email status
    POST /reports/{id}/send         email the saved analysis to the IT distribution list

Every route except /config answers 404 unless ENABLE_TICKET_REPORTS=true. Any signed-in user may run it (there are no
roles yet), so the feature is off by default and the recipient is fixed in the backend's configuration.
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import get_db
from app.db.models import TicketReport
from app.schemas.ticket_report import ReportConfig, ReportRead, SendResult
from app.services import ticket_report_service as service
from app.services.ticket_report_parser import ReportUploadError

settings = get_settings()

router = APIRouter(prefix="/reports", tags=["reports"])

UPLOAD_PATH_SUFFIX = "/reports/upload"
_MULTIPART_OVERHEAD = 64 * 1024


def require_enabled() -> None:
    if not service.feature_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def _read_model(report: TicketReport) -> ReportRead:
    can_send, blocked = service.can_send(report)
    last_attempt = (report.email_log or [{}])[-1].get("at") if report.email_log else None
    validation = report.validation or {}
    return ReportRead.model_validate({
        "id": str(report.id),
        "filename": report.filename,
        "file_type": report.file_type,
        "status": report.status,
        "created_at": report.created_at,
        "source_row_count": report.source_row_count,
        "row_count": report.row_count,
        "columns": report.detected_columns or [],
        "validation": {
            "valid": bool(validation.get("valid")),
            "errors": validation.get("errors", []),
            "warnings": validation.get("warnings", []),
            "fields_present": validation.get("fields_present", []),
        },
        "can_analyze": bool(validation.get("valid") and report.dataset and report.status in (service.UPLOADED, service.FAILED)),
        "progress": report.progress if report.status == service.ANALYZING else None,
        "error": report.error,
        "analysis": report.analysis if report.status == service.COMPLETED else None,
        "analysis_completed_at": report.analysis_completed_at,
        "email": {
            "status": report.email_status,
            "recipient": report.email_recipient,
            "attempts": report.email_attempts,
            "sent_at": report.email_sent_at,
            "last_attempt_at": last_attempt,
            "error": report.email_error,
            "can_send": can_send,
            "blocked_reason": blocked,
        },
    })


async def _load(db: AsyncSession, report_id: uuid.UUID) -> TicketReport:
    report = await service.get_report(db, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    return report


@router.get("/config", response_model=ReportConfig)
async def report_config() -> ReportConfig:
    return ReportConfig.model_validate(service.public_config())


@router.post("/upload", response_model=ReportRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_enabled)])
async def upload_report(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)) -> ReportRead:
    limit = settings.ticket_report_max_upload_bytes
    content = await file.read(limit + 1)  # one byte more than allowed is enough to know it is too big
    try:
        report = await service.create_upload(db, file.filename, content)
    except ReportUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _read_model(report)


@router.post("/{report_id}/analyze", response_model=ReportRead, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_enabled)])
async def analyze_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ReportRead:
    try:
        report = await service.claim_analysis(db, report_id)
    except service.ReportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    service.start_analysis(report.id)
    return _read_model(report)


@router.get("/{report_id}", response_model=ReportRead, dependencies=[Depends(require_enabled)])
async def get_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ReportRead:
    return _read_model(await _load(db, report_id))


@router.post("/{report_id}/send", response_model=SendResult, dependencies=[Depends(require_enabled)])
async def send_report(report_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await _load(db, report_id)  # 404 before anything else
    try:
        result = await service.send_report(report_id)
    except service.ReportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    report = result["report"]
    body = SendResult(
        report=_read_model(report),
        already_sent=result["already_sent"],
        message=(
            "This report was already sent; nothing was sent again." if result["already_sent"]
            else f"The report was sent to {report.email_recipient}." if report.email_status == service.SENT
            else service.SEND_FAILED_MESSAGE
        ),
    )
    if report.email_status != service.SENT:
        # The report is in the body so the dashboard can show the failed attempt; the status says it did not go.
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": body.message, **body.model_dump(mode="json")})
    return body


class UploadSizeLimitMiddleware:
    """Refuse an oversized report upload from its Content-Length header, before the multipart body is read.

    The route re-checks the real byte count, which also covers a chunked upload that sends no Content-Length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith(UPLOAD_PATH_SUFFIX):
            length = dict(scope.get("headers") or []).get(b"content-length")
            limit = settings.ticket_report_max_upload_bytes + _MULTIPART_OVERHEAD
            if length is not None and length.isdigit() and int(length) > limit:
                response = JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": f"The file is larger than the {settings.ticket_report_max_upload_bytes // 1_000_000} MB limit."},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
