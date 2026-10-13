"""Админский роутер.

Проверка прав навешивается здесь, на роутер целиком: хендлеры внутри
не могут её обойти, потому что просто не получат управления.
Разделение по ролям:
  механик — только парк,
  администратор — всё, кроме сотрудников,
  владелец — всё.
"""
from __future__ import annotations

from aiogram import Router

from app.db.models import UserRole
from app.handlers.admin import (
    bookings,
    clients,
    broadcast,
    content,
    fleet,
    group,
    menu,
    photos,
    staff,
    stats,
)
from app.middlewares.auth import RoleMiddleware


def build_admin_router() -> Router:
    router = Router(name="admin")

    # Парк доступен и механику
    staff_router = Router(name="admin_staff_area")
    staff_router.include_router(menu.router)
    staff_router.include_router(fleet.router)
    _guard(staff_router, UserRole.MECHANIC, UserRole.ADMIN, UserRole.OWNER)

    # Остальное — только администраторам
    admin_router = Router(name="admin_area")
    admin_router.include_router(bookings.router)
    admin_router.include_router(group.router)
    admin_router.include_router(broadcast.router)
    admin_router.include_router(content.router)
    admin_router.include_router(photos.router)
    admin_router.include_router(clients.router)
    admin_router.include_router(stats.router)
    _guard(admin_router, UserRole.ADMIN, UserRole.OWNER)

    # Сотрудники — только владельцу
    owner_router = Router(name="owner_area")
    owner_router.include_router(staff.router)
    _guard(owner_router, UserRole.OWNER)

    router.include_router(staff_router)
    router.include_router(admin_router)
    router.include_router(owner_router)
    return router


def _guard(router: Router, *roles: UserRole) -> None:
    guard = RoleMiddleware(*roles)
    router.message.middleware(guard)
    router.callback_query.middleware(guard)
