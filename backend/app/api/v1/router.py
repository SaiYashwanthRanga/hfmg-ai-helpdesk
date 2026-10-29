from fastapi import APIRouter, Depends

from app.api.v1 import ai_insights, analytics, auth, categories, health, reports, settings, tickets, voice_calls
from app.core.auth import require_login
from app.simulator import routes as simulator_routes
from app.voice import sip_routes

api_router = APIRouter(prefix="/api/v1")
# Everything the dashboard reads or changes needs a signed-in session (app/core/auth.py).
signed_in = [Depends(require_login)]

# /health and /health/ready stay open for probes; /health/dependencies requires sign-in itself.
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(tickets.router, dependencies=signed_in)
api_router.include_router(categories.router, dependencies=signed_in)
api_router.include_router(settings.router, dependencies=signed_in)
api_router.include_router(analytics.router, dependencies=signed_in)
api_router.include_router(voice_calls.router, dependencies=signed_in)
api_router.include_router(ai_insights.router, dependencies=signed_in)
# Every route except /reports/config returns 404 unless ENABLE_TICKET_REPORTS=true.
api_router.include_router(reports.router, dependencies=signed_in)
# Every route returns 404 unless ENABLE_VOICE_SIMULATOR=true outside production.
api_router.include_router(simulator_routes.router, dependencies=signed_in)
# The SIP gateway authenticates with its own Bearer token, not a dashboard session.
api_router.include_router(sip_routes.router)
