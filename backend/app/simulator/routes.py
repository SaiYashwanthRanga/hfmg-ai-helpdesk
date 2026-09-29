"""AI Call Simulator HTTP API (VOICE_SIMULATOR_DESIGN.md §3).

Thin by design, like voice/routes.py: validate, delegate to the service.
Every route is behind `require_simulator_enabled`, which answers 404 when the
feature is off so a disabled deployment does not advertise it.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.base import get_db
from app.llm.factory import get_provider
from app.simulator import metrics, mock_callers, schemas, service
from app.speech.factory import get_speech_provider
from app.voice.nlu import VOICE_CATEGORIES

settings = get_settings()


def simulator_allowed() -> bool:
    """Checked per request as well as at startup (config.py), so a setting
    flipped at runtime -- including in tests -- can never expose the simulator
    in production."""
    production = settings.environment.strip().lower() in ("production", "prod")
    return settings.enable_voice_simulator and not production


def require_simulator_enabled() -> None:
    if not simulator_allowed():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


AUDIO_PATH_SUFFIX = "/voice-simulator/audio"
# Multipart framing around the audio part (boundaries, field headers, the
# two small form fields) is well under this.
_MULTIPART_OVERHEAD = 64 * 1024


class AudioSizeLimitMiddleware:
    """Reject an oversized /audio upload from its Content-Length header,
    before Starlette parses (and spools) the multipart body.

    The route re-checks the actual byte count, which also covers chunked
    uploads that send no Content-Length.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith(AUDIO_PATH_SUFFIX):
            headers = dict(scope.get("headers") or [])
            length = headers.get(b"content-length")
            limit = settings.simulator_max_audio_bytes + _MULTIPART_OVERHEAD
            if length is not None and length.isdigit() and int(length) > limit:
                response = JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": f"Audio exceeds {settings.simulator_max_audio_bytes} bytes"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


router = APIRouter(
    prefix="/voice-simulator",
    tags=["voice-simulator"],
    dependencies=[Depends(require_simulator_enabled)],
)


@router.get("/config", response_model=schemas.SimulatorConfigResponse)
async def get_config() -> schemas.SimulatorConfigResponse:
    return schemas.SimulatorConfigResponse(
        enabled=True,
        speech_configured=get_speech_provider().is_configured,
        llm_configured=get_provider().is_configured,
        stt_model=settings.speech_stt_model,
        tts_model=settings.speech_tts_model,
        tts_voice=settings.speech_tts_voice,
        max_audio_bytes=settings.simulator_max_audio_bytes,
        max_turns_per_session=settings.simulator_max_turns_per_session,
        max_concurrent_sessions=settings.simulator_max_concurrent_sessions,
        notifications_allowed=settings.simulator_allow_notifications,
        voice_categories=list(VOICE_CATEGORIES),
    )


@router.post("/start", response_model=schemas.StartResponse, status_code=status.HTTP_201_CREATED)
async def start(request: schemas.StartRequest, db: AsyncSession = Depends(get_db)) -> schemas.StartResponse:
    return await service.start_session(db, request)


@router.post("/audio", response_model=schemas.AudioResponse)
async def audio(
    session_id: uuid.UUID = Form(...),
    turn_client_id: uuid.UUID = Form(...),
    audio: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> schemas.AudioResponse:
    # Read one byte past the cap so an oversized upload is rejected without
    # buffering all of it.
    data = await audio.read(settings.simulator_max_audio_bytes + 1)
    return await service.transcribe(
        db,
        session_id=session_id,
        turn_client_id=turn_client_id,
        audio=data,
        mime_type=audio.content_type or "",
    )


@router.post("/process", response_model=schemas.ProcessResponse)
async def process(
    request: schemas.ProcessRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> schemas.ProcessResponse:
    return await service.process_turn(db, background_tasks, request)


@router.get("/speech/{turn_id}")
async def speech(turn_id: uuid.UUID) -> StreamingResponse:
    """The agent's spoken reply for one turn, streamed as it is synthesized."""
    body, media_type = await service.stream_speech(turn_id)
    return StreamingResponse(body, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.post("/end", response_model=schemas.SimulatorSession)
async def end(
    request: schemas.EndRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> schemas.SimulatorSession:
    return await service.end_session(db, background_tasks, request)


@router.get("/session/{session_id}", response_model=schemas.SessionDetail)
async def get_session(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> schemas.SessionDetail:
    return await service.get_detail(db, session_id)


@router.get("/metrics/{session_id}", response_model=schemas.MetricsResponse)
async def get_metrics(session_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> schemas.MetricsResponse:
    return await metrics.session_metrics(db, session_id)


@router.post("/session/{session_id}/client-metrics", response_model=schemas.TurnTimings)
async def client_metrics(
    session_id: uuid.UUID,
    request: schemas.ClientMetricsRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.TurnTimings:
    return await service.record_client_metrics(db, session_id, request)


@router.get("/stats", response_model=schemas.SimulatorStatsResponse)
async def stats(
    hours: int = Query(24, ge=1, le=24 * 30), db: AsyncSession = Depends(get_db)
) -> schemas.SimulatorStatsResponse:
    return await metrics.window_stats(db, hours=hours)


@router.get("/random-issue", response_model=schemas.RandomIssue)
async def random_issue(
    seed: int = Query(..., ge=0, le=2**31 - 1),
    category: str | None = Query(None, max_length=64),
) -> schemas.RandomIssue:
    try:
        issue = mock_callers.random_issue(seed, category)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return schemas.RandomIssue(
        seed=seed, category=issue.category, priority=issue.priority, utterance=issue.utterance, note=issue.note
    )


@router.get("/mock-caller", response_model=schemas.MockCaller)
async def mock_caller(
    seed: int = Query(..., ge=0, le=2**31 - 1),
    category: str | None = Query(None, max_length=64),
    escalate: bool = False,
) -> schemas.MockCaller:
    try:
        return schemas.MockCaller(**mock_callers.mock_caller(seed, category=category, escalate=escalate))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
