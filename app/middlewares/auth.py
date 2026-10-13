"""Определение пользователя и проверка прав.

Два отдельных middleware:
  AuthMiddleware — вешается на всё, находит или заводит пользователя.
  RoleMiddleware — вешается на защищённый роутер, пускает только своих.

Разделение важно: проверка прав живёт не внутри хендлеров, а на входе
в роутер. Забыть её в новом хендлере физически нельзя — он просто
не получит управления.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, UserRole
from app.utils.logger import get_logger

log = get_logger(__name__)


async def get_or_create_user(session: AsyncSession, tg_user: TgUser) -> User:
    """Находит пользователя по Telegram ID или заводит нового."""
    user = await session.scalar(select(User).where(User.tg_id == tg_user.id))

    if user is None:
        role = (
            UserRole.OWNER if tg_user.id == settings.owner_tg_id else UserRole.USER
        )
        user = User(
            tg_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name or "",
            role=role,
        )
        session.add(user)
        await session.flush()
        log.info("Новый пользователь id=%s tg_id=%s", user.id, tg_user.id)
        return user

    # Имя и ник в Telegram меняются — поддерживаем актуальность
    changed = False
    if tg_user.username != user.username:
        user.username = tg_user.username
        changed = True
    if tg_user.full_name and tg_user.full_name != user.full_name:
        user.full_name = tg_user.full_name
        changed = True
    if changed:
        await session.flush()

    return user


class AuthMiddleware(BaseMiddleware):
    """Кладёт в data объект User под ключом 'user'."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")

        if tg_user is None or session is None or tg_user.is_bot:
            return await handler(event, data)

        user = await get_or_create_user(session, tg_user)

        if user.is_blocked:
            log.warning("Заблокированный пользователь tg_id=%s", tg_user.id)
            if isinstance(event, Message):
                await event.answer("Доступ к боту ограничен.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ к боту ограничен.", show_alert=True)
            return None

        data["user"] = user
        return await handler(event, data)


class RoleMiddleware(BaseMiddleware):
    """Пропускает только пользователей с нужной ролью.

    Вешается на роутер целиком:
        admin_router.message.middleware(RoleMiddleware(UserRole.ADMIN, UserRole.OWNER))
        admin_router.callback_query.middleware(...)
    """

    def __init__(self, *allowed: UserRole):
        self.allowed = set(allowed)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("user")

        if user is None or user.role not in self.allowed:
            tg_id = getattr(data.get("event_from_user"), "id", "?")
            log.warning(
                "Отказ в доступе: tg_id=%s роль=%s требуется=%s",
                tg_id,
                user.role.value if user else "нет",
                [r.value for r in self.allowed],
            )
            # Отвечаем нейтрально: не подтверждаем существование раздела
            if isinstance(event, CallbackQuery):
                await event.answer("Раздел недоступен.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("Неизвестная команда.")
            return None

        return await handler(event, data)
