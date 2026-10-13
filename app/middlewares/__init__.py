"""Подключение middleware к диспетчеру."""
from __future__ import annotations

from aiogram import Dispatcher

from app.middlewares.auth import AuthMiddleware, RoleMiddleware, get_or_create_user
from app.middlewares.db import DatabaseMiddleware
from app.middlewares.notices import NoticeCleanupMiddleware
from app.middlewares.throttling import ThrottlingMiddleware

__all__ = [
    "AuthMiddleware",
    "RoleMiddleware",
    "DatabaseMiddleware",
    "NoticeCleanupMiddleware",
    "ThrottlingMiddleware",
    "get_or_create_user",
    "setup_middlewares",
]


def setup_middlewares(dp: Dispatcher) -> None:
    """Порядок важен: сначала троттлинг (чтобы отсечь лишнее до БД),
    затем уборка уведомлений (ей не нужна БД), затем сессия, затем
    пользователь (ему нужна сессия)."""
    for observer in (dp.message, dp.callback_query):
        observer.middleware(ThrottlingMiddleware())
        observer.middleware(NoticeCleanupMiddleware())
        observer.middleware(DatabaseMiddleware())
        observer.middleware(AuthMiddleware())
