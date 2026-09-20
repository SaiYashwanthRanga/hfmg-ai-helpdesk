from fastapi import APIRouter

from app.api.v1 import ai_insights, analytics, categories, health, settings, tickets, voice_calls
from app.voice import routes as voice_routes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(tickets.router)
api_router.include_router(categories.router)
api_router.include_router(settings.router)
api_router.include_router(analytics.router)
api_router.include_router(voice_calls.router)
api_router.include_router(ai_insights.router)
api_router.include_router(voice_routes.router)
