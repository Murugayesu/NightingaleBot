from fastapi import APIRouter

from nursing_bot.api.v1.admin import router as admin_router
from nursing_bot.api.v1.chat import router as chat_router
from nursing_bot.api.v1.documents import router as documents_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(chat_router)
v1_router.include_router(documents_router)
v1_router.include_router(admin_router)

__all__ = ["admin_router", "chat_router", "documents_router", "v1_router"]
