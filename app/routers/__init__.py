from fastapi import APIRouter

from app.routers import events, tags, uploads, users

api_router = APIRouter()
api_router.include_router(events.router)
api_router.include_router(tags.router)
api_router.include_router(uploads.router)
api_router.include_router(users.router)
