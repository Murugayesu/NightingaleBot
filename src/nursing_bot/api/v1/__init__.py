from fastapi import APIRouter

from nursing_bot.api.v1.accounts import router as accounts_router
from nursing_bot.api.v1.admin import router as admin_router
from nursing_bot.api.v1.admin_users import router as admin_users_router
from nursing_bot.api.v1.chat import router as chat_router
from nursing_bot.api.v1.conversations import router as conversations_router
from nursing_bot.api.v1.documents import router as documents_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(chat_router)
v1_router.include_router(accounts_router)
v1_router.include_router(conversations_router)
v1_router.include_router(documents_router)
v1_router.include_router(admin_router)
v1_router.include_router(admin_users_router)

# Direct /api route aliases for local-ui and unversioned clients
api_router = APIRouter(prefix="/api")
api_router.include_router(chat_router)
api_router.include_router(accounts_router)
api_router.include_router(conversations_router)
api_router.include_router(documents_router)
api_router.include_router(admin_router)
api_router.include_router(admin_users_router)

__all__ = [
    "accounts_router",
    "admin_router",
    "admin_users_router",
    "api_router",
    "chat_router",
    "conversations_router",
    "documents_router",
    "v1_router",
]
