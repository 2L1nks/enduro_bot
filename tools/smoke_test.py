#!/usr/bin/env python3
"""Прогон бизнес-логики напрямую, в обход Telegram.

Выполняет те же вызовы, что делают хендлеры админки, но на вашей
реальной базе и с настоящими трассировками. Если что-то в админке
«не работает», этот скрипт покажет, что именно ломается.

Пишет только в тестовые записи и в конце всё за собой убирает.

Запуск из папки проекта с активированным окружением:
    python tools/smoke_test.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

passed = 0
failed = 0


def ok(label: str, detail: str = "") -> None:
    global passed
    passed += 1
    print(f"  OK   {label}" + (f" — {detail}" if detail else ""))


def bad(label: str, exc: BaseException) -> None:
    global failed
    failed += 1
    print(f"  ✖    {label}")
    print("       " + "".join(
        traceback.format_exception_only(type(exc), exc)
    ).strip())
    print("       ── полная трассировка ──")
    for line in traceback.format_exc().splitlines()[-12:]:
        print("       " + line)


async def main() -> int:
    global failed

    print("Проверка бизнес-логики на живой базе")
    print("─" * 55)

    from app.config import settings
    from app.db.models import Experience, MotorcycleStatus, User, UserRole
    from app.db.session import engine, session_factory
    from app.utils.timeslots import TimeWindow

    created_moto_id: int | None = None
    created_broadcast_id: int | None = None

    # ---------- 1. Подключение ----------
    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        ok("подключение к базе")
    except Exception as exc:
        bad("подключение к базе", exc)
        print("\nДальше проверять нечего: база недоступна.")
        return 1

    # ---------- 2. Владелец на месте ----------
    async with session_factory() as session:
        from sqlalchemy import select

        owner = await session.scalar(
            select(User).where(User.tg_id == settings.owner_tg_id)
        )
        if owner is None:
            print(f"  ✖    владельца с tg_id={settings.owner_tg_id} нет в базе")
            failed += 1
            return 1
        ok("владелец найден", f"роль {owner.role.value}")
        if owner.role != UserRole.OWNER:
            print("       ⚠ роль не OWNER — админка будет недоступна")

    # ---------- 3. Добавление мотоцикла ----------
    async with session_factory() as session:
        try:
            from app.services import fleet as fleet_service

            moto = await fleet_service.create(
                session,
                title="__СМОУК__ Тестовый",
                model="Test Model",
                reg_number=None,
                category=Experience.BEGINNER,
                min_rider_height_cm=165,
                max_rider_height_cm=190,
                max_rider_weight_kg=100,
                actor_id=owner.id,
            )
            await session.commit()
            created_moto_id = moto.id
            ok("создание мотоцикла", f"id={moto.id}")
        except Exception as exc:
            await session.rollback()
            bad("создание мотоцикла", exc)

    # ---------- 4. Карточка и клавиатуры ----------
    if created_moto_id:
        async with session_factory() as session:
            try:
                from app.keyboards.admin import (
                    fleet_list,
                    moto_card_keyboard,
                    status_choice,
                )
                from app.repositories import motorcycle as moto_repo
                from app.utils.formatters import moto_card

                moto = await moto_repo.get(session, created_moto_id)
                card = moto_card(moto)
                moto_card_keyboard(moto)
                status_choice(moto.id)
                fleet_list(await moto_repo.list_all(session))
                ok("карточка и клавиатуры парка", f"{len(card)} символов")
            except Exception as exc:
                bad("карточка и клавиатуры парка", exc)

    # ---------- 5. Смена статуса ----------
    if created_moto_id:
        async with session_factory() as session:
            try:
                from app.services import fleet as fleet_service

                await fleet_service.set_status(
                    session, created_moto_id, MotorcycleStatus.MAINTENANCE,
                    actor_id=owner.id,
                )
                await session.commit()
                ok("смена статуса техники")
            except Exception as exc:
                await session.rollback()
                bad("смена статуса техники", exc)

    # ---------- 6. Обслуживание ----------
    if created_moto_id:
        async with session_factory() as session:
            try:
                from app.services import fleet as fleet_service

                start = datetime.now(tz=settings.tz) + timedelta(days=200)
                window = TimeWindow(start, start + timedelta(days=2))
                period, conflicts = await fleet_service.schedule_maintenance(
                    session, created_moto_id, window, "смоук-тест",
                    actor_id=owner.id,
                )
                await session.commit()
                ok("вывод на обслуживание",
                   f"создан={period is not None}, конфликтов={len(conflicts)}")
            except Exception as exc:
                await session.rollback()
                bad("вывод на обслуживание", exc)

    # ---------- 6b. Возврат в парк и снятие простоя ----------
    if created_moto_id:
        async with session_factory() as session:
            try:
                from app.repositories import motorcycle as moto_repo
                from app.services import fleet as fleet_service

                await fleet_service.retire(session, created_moto_id, actor_id=owner.id)
                await session.commit()
                moto = await moto_repo.get(session, created_moto_id)
                was_hidden = not moto.is_active

                await fleet_service.restore(session, created_moto_id, actor_id=owner.id)
                await session.commit()
                moto = await moto_repo.get(session, created_moto_id)
                ok("вывод и возврат в парк",
                   f"скрывался={was_hidden}, вернулся={moto.is_active}")
            except Exception as exc:
                await session.rollback()
                bad("вывод и возврат в парк", exc)

        async with session_factory() as session:
            try:
                from app.repositories import motorcycle as moto_repo
                from app.services import fleet as fleet_service

                periods = await moto_repo.maintenance_periods(session, created_moto_id)
                if periods:
                    await fleet_service.cancel_maintenance(
                        session, periods[0].id, actor_id=owner.id
                    )
                    await session.commit()
                    left = await moto_repo.maintenance_periods(session, created_moto_id)
                    ok("снятие планового простоя", f"осталось периодов {len(left)}")
                else:
                    ok("снятие планового простоя", "периодов не было")
            except Exception as exc:
                await session.rollback()
                bad("снятие планового простоя", exc)

    # ---------- 7. Доступность и календарь ----------
    async with session_factory() as session:
        try:
            from app.services import availability as avail

            now = datetime.now(tz=settings.tz)
            overview = await avail.month_overview(
                session, now.year, now.month, settings.rental_durations[0]
            )
            free_today = sum(d.free_count for d in overview.values())
            ok("календарь на месяц",
               f"{len(overview)} дней, сумма свободных {free_today}")
        except Exception as exc:
            bad("календарь на месяц", exc)

    # ---------- 8. Аудитория рассылки ----------
    async with session_factory() as session:
        try:
            from app.repositories import admin as admin_repo

            everyone = await admin_repo.broadcast_audience(session, "all")
            upcoming = await admin_repo.broadcast_audience(session, "upcoming")
            ok("аудитория рассылки",
               f"всем={len(everyone)}, ближайшим={len(upcoming)}")
            if not everyone:
                print("       ⚠ получателей нет — рассылать некому")
        except Exception as exc:
            bad("аудитория рассылки", exc)

    # ---------- 9. Постановка рассылки в очередь ----------
    async with session_factory() as session:
        try:
            from app.db.models import Broadcast
            from app.repositories import admin as admin_repo
            from app.services import notifier

            recipients = await admin_repo.broadcast_audience(session, "all")
            broadcast = Broadcast(
                author_id=owner.id,
                text="__СМОУК__ проверка",
                audience={"type": "all"},
                total=len(recipients),
            )
            session.add(broadcast)
            await session.flush()
            created_broadcast_id = broadcast.id

            queued = await notifier.queue_broadcast(
                session, recipients, "__СМОУК__ проверка",
                broadcast_id=broadcast.id,
            )
            await session.commit()
            ok("постановка рассылки в очередь", f"{queued} сообщений")
        except Exception as exc:
            await session.rollback()
            bad("постановка рассылки в очередь", exc)

    # ---------- 10. Выборка воркера ----------
    async with session_factory() as session:
        try:
            from app.services import notifier

            due = await notifier.due_notifications(session, limit=100)
            rendered = [notifier.render(n) for n in due]
            empty = sum(1 for r in rendered if not r.strip())
            await session.rollback()  # блокировки не держим
            ok("воркер видит очередь",
               f"к отправке {len(due)}, пустых текстов {empty}")
            if due and empty:
                print("       ⚠ есть уведомления с пустым текстом — не уйдут")
        except Exception as exc:
            await session.rollback()
            bad("воркер видит очередь", exc)

    # ---------- 11. Статистика и экспорт ----------
    async with session_factory() as session:
        try:
            from app.repositories import admin as admin_repo
            from app.services import export as export_service

            from app.handlers.admin.stats import _month_bounds

            now = datetime.now(tz=settings.tz)
            since, until = _month_bounds(now.year, now.month)
            report = await admin_repo.month_report(session, since, until, now)
            assert report.debt >= 0, "долг не может быть отрицательным"
            await admin_repo.top_clients(session, limit=5)
            data, name = await export_service.export_xlsx(session)
            ok("статистика и экспорт", f"{name}, {len(data)} байт")
        except Exception as exc:
            bad("статистика и экспорт", exc)

    # ---------- 12. Хранилище фото ----------
    try:
        from app.services.storage import get_storage

        healthy = await get_storage().health()
        if healthy:
            ok("хранилище фото доступно")
        else:
            print("  ✖    хранилище фото недоступно")
            failed += 1
    except Exception as exc:
        bad("хранилище фото", exc)

    # ---------- Уборка ----------
    print("\nУбираю тестовые данные…")
    async with session_factory() as session:
        try:
            from sqlalchemy import delete

            from app.db.models import (
                MaintenancePeriod,
                Motorcycle,
                Notification,
            )

            if created_broadcast_id:
                await session.execute(
                    delete(Notification).where(
                        Notification.broadcast_id == created_broadcast_id
                    )
                )
                from app.db.models import Broadcast

                await session.execute(
                    delete(Broadcast).where(Broadcast.id == created_broadcast_id)
                )
            if created_moto_id:
                await session.execute(
                    delete(MaintenancePeriod).where(
                        MaintenancePeriod.motorcycle_id == created_moto_id
                    )
                )
                await session.execute(
                    delete(Motorcycle).where(Motorcycle.id == created_moto_id)
                )
            await session.commit()
            print("Готово.")
        except Exception as exc:
            await session.rollback()
            print(f"Не удалось убрать тестовые данные: {exc}")
            print("Записи помечены «__СМОУК__» — можно удалить вручную.")

    await engine.dispose()

    print("─" * 55)
    print(f"Успешно: {passed}, с ошибками: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
