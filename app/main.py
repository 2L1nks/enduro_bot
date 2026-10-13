"""Точка входа. На шаге 1 — только каркас и проверка связности.

Роутеры, middleware и планировщик подключаются в следующих модулях
в отмеченных местах.
"""
from __future__ import annotations

import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from sqlalchemy import text

from app.config import settings
from app.db.bootstrap import bootstrap
from app.db.schema_check import explain, missing_requirements
from app.db.session import engine
from app.handlers import setup_routers
from app.middlewares import setup_middlewares
from app.scheduler import start_scheduler, stop_scheduler
from app.utils.logger import get_logger, setup_logging

log = get_logger(__name__)


async def check_database() -> None:
    """Падаем на старте, а не на первом хендлере, если БД не готова."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        has_ext = await conn.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'btree_gist'")
        )
        if not has_ext:
            raise RuntimeError(
                "Расширение btree_gist не установлено — миграции не применены. "
                "Выполните: alembic upgrade head"
            )

        # Не запускаемся на старой схеме: иначе бот работает до первого
        # нажатия, которое упирается в отсутствующую колонку
        missing = await missing_requirements(conn)
        if missing:
            log.error(explain(missing))
            raise SystemExit(1)
    log.info("База данных доступна, схема актуальна")


async def check_storage() -> None:
    """Предупреждаем о недоступном хранилище на старте, а не при первом фото."""
    if settings.storage_backend == "yadisk":
        from app.services.yadisk import get_disk

        if await get_disk().health():
            log.info("Яндекс.Диск доступен, папка %s", settings.yandex_disk_root)
        else:
            log.warning(
                "Яндекс.Диск недоступен — загрузка фото работать не будет. "
                "Проверьте YANDEX_DISK_TOKEN."
            )
        return

    from app.services.storage import get_storage

    if await get_storage().health():
        log.info("Хранилище фото доступно")
    else:
        log.warning(
            "Хранилище фото недоступно — загрузка снимков работать не будет. "
            "Остальные функции не затронуты."
        )


def build_storage() -> BaseStorage:
    """MemoryStorage для разработки, Redis для прода.

    На Windows Redis нативно не ставится, поэтому по умолчанию память.
    Минус памяти: при перезапуске бота наполовину заполненная форма
    админа теряется. Для боевого сервера ставьте FSM_STORAGE=redis.
    """
    if settings.fsm_storage == "redis":
        from aiogram.fsm.storage.redis import RedisStorage

        log.info("FSM: Redis (%s)", settings.redis_url)
        return RedisStorage.from_url(settings.redis_url)

    log.warning(
        "FSM: память — незаконченные формы потеряются при перезапуске. "
        "Для продакшена задайте FSM_STORAGE=redis"
    )
    return MemoryStorage()


async def main() -> None:
    setup_logging()
    log.info("Запуск бота")

    await check_database()
    await bootstrap()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = build_storage()
    dp = Dispatcher(storage=storage)

    setup_middlewares(dp)
    setup_routers(dp)

    await check_storage()

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="menu", description="Вернуться в меню"),
            BotCommand(command="admin", description="Админ-панель"),
            BotCommand(command="help", description="Справка"),
        ]
    )

    # Воркер напоминаний живёт фоновой задачей рядом с поллингом
    start_scheduler(bot)

    me = await bot.get_me()
    log.info("Бот @%s готов к работе", me.username)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await stop_scheduler()
        await bot.session.close()
        await storage.close()
        await engine.dispose()
        log.info("Бот остановлен")


if __name__ == "__main__":
    if sys.platform == "win32":
        # На Windows по умолчанию ProactorEventLoop, на котором asyncpg
        # ведёт себя неустойчиво при закрытии пула. Selector надёжнее,
        # aiogram с ним работает без оговорок.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Выход по сигналу")
    # SystemExit не глушим: при устаревшей схеме причина уже в логе,
    # а ненулевой код выхода сразу виден в консоли
