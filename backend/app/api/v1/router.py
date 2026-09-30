from fastapi import APIRouter

from app.api.v1 import ai_insights, analytics, categories, health, settings, tickets, voice_calls
from app.simulator import routes as simulator_routes
from app.voice import routes as voice_routes
from app.voice import sip_routes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(tickets.router)
api_router.include_router(categories.router)
api_router.include_router(settings.router)
api_router.include_router(analytics.router)
api_router.include_router(voice_calls.router)
api_router.include_router(ai_insights.router)
api_router.include_router(voice_routes.router)
# Every route returns 404 unless ENABLE_VOICE_SIMULATOR=true outside production.
api_router.include_router(simulator_routes.router)
api_router.include_router(sip_routes.router)
