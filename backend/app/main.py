import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("hfmg.main")

settings = get_settings()

app = FastAPI(title="HFMG AI Help Desk API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for anything a route/service raised without handling.

    Without this, an unexpected error (e.g. a DB error not caught in a
    service function) falls through to Starlette's default handler, which
    still returns 500 but never goes through this app's own logger -- so it
    is easy to miss in production log aggregation. `HTTPException` and
    `RequestValidationError` are re-raised as-is so their normal, more
    specific status codes/bodies are preserved unchanged (this handler only
    widens coverage for genuinely unhandled exceptions, it does not change
    any existing response shape).
    """
    if isinstance(exc, (HTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(api_router)
