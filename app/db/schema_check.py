"""Сверка схемы базы с тем, что ждёт код.

Обновления схемы на Windows накатываются руками в pgAdmin. Если файл
забыли выполнить, бот запускался как ни в чём не бывало и падал
посреди сценария — например, при отметке оплаты. Здесь всё нужное
проверяется на старте, и в лог пишется, какой именно файл выполнить.

Проверяются сами колонки, а не номер в alembic_version: номер ставит
тот же скрипт, и по нему нельзя понять, что реально применилось.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class Requirement:
    what: str     # что сломается без этого — человеческими словами
    sql: str      # запрос, возвращающий true, если всё на месте
    fix: str      # какой файл выполнить в pgAdmin


def _column_exists(table: str, column: str) -> str:
    return (
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name = '{column}')"
    )


def _column_nullable(table: str, column: str) -> str:
    return (
        "SELECT COALESCE((SELECT is_nullable = 'YES' "
        "FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name = '{column}'), false)"
    )


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        "участники без телеграм-аккаунта",
        _column_nullable("booking_participants", "user_id"),
        "docs/upgrade_0003_0004.sql",
    ),
    Requirement(
        "телефон в записи",
        _column_exists("bookings", "contact_phone"),
        "docs/upgrade_0003_0004.sql",
    ),
    Requirement(
        "папки клиентов на Яндекс.Диске",
        _column_exists("users", "disk_folder"),
        "docs/upgrade_0003_0004.sql",
    ),
    Requirement(
        "фото на Яндекс.Диске",
        _column_exists("photos", "disk_path"),
        "docs/upgrade_0003_0004.sql",
    ),
    Requirement(
        "оплата за группу без аккаунтов",
        _column_nullable("payments", "user_id"),
        "docs/upgrade_0005.sql",
    ),
    Requirement(
        "аккаунты людей без телеграма",
        _column_nullable("users", "tg_id"),
        "docs/upgrade_0006.sql",
    ),
    Requirement(
        "опознание по ФИО",
        _column_exists("users", "name_key"),
        "docs/upgrade_0006.sql",
    ),
)


async def missing_requirements(conn: AsyncConnection) -> list[Requirement]:
    """Что из нужного коду в базе отсутствует."""
    missing: list[Requirement] = []
    for req in REQUIREMENTS:
        if not await conn.scalar(text(req.sql)):
            missing.append(req)
    return missing


def explain(missing: list[Requirement]) -> str:
    """Сообщение для лога: что выполнить и зачем, по порядку файлов."""
    files: dict[str, list[str]] = {}
    for req in missing:
        files.setdefault(req.fix, []).append(req.what)

    lines = ["База не обновлена до текущей версии бота.",
             "Выполните в pgAdmin (Query Tool), по порядку:"]
    for path in sorted(files):
        lines.append(f"  • {path} — {', '.join(files[path])}")
    lines.append("Файлы безопасно запускать повторно. Затем перезапустите бота.")
    return "\n".join(lines)
