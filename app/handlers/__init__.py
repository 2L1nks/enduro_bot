"""Сборка роутеров.

Порядок важен. Админский роутер идёт раньше пользовательского:
кнопку «Админка» обрабатывает он, и если бы первым стоял
пользовательский, тот перехватил бы её своим обработчиком меню.
Перехват ошибок — последним, он ловит всё, что не разобрали выше.
"""
from __future__ import annotations

from aiogram import Dispatcher, Router

from app.handlers.admin import build_admin_router
from app.handlers.common import errors, fallback
from app.handlers.user import booking, cabinet, info, photos, start


def build_user_router() -> Router:
    router = Router(name="user")
    router.include_router(start.router)
    router.include_router(booking.router)
    router.include_router(cabinet.router)
    router.include_router(photos.router)
    router.include_router(info.router)
    return router


def setup_routers(dp: Dispatcher) -> None:
    dp.include_router(build_admin_router())
    dp.include_router(build_user_router())
    # Ловит всё, что не разобрали выше, — должен идти предпоследним
    dp.include_router(fallback.router)
    dp.include_router(errors.router)
