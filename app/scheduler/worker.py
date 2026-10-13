"""Воркер рассылки уведомлений.

Раз в минуту забирает из очереди всё, чему пришло время, и отправляет.
Своего состояния не держит: при падении бота ничего не теряется,
после рестарта воркер просто продолжит с того же места.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import update

from app.config import settings
from app.db.models import (
    Booking,
    BookingStatus,
    Notification,
    NotificationStatus,
    User,
)
from app.db.session import session_factory
from app.services import notifier
from app.utils.logger import get_logger

log = get_logger(__name__)

#: Как часто проверять очередь
TICK_SECONDS = 60
#: Пауза между сообщениями: Telegram пропускает около 30 в секунду,
#: берём с запасом, чтобы не ловить 429 на больших рассылках
SEND_DELAY = 0.05
#: Сколько раз пробовать доставить, прежде чем сдаться
MAX_ATTEMPTS = 3


async def _send_one(bot: Bot, session, notification: Notification) -> bool:
    """Отправляет одно уведомление. True — доставлено."""
    user = await session.get(User, notification.user_id)
    if user is None or user.is_blocked:
        notification.status = NotificationStatus.CANCELLED
        return False
    if not user.has_telegram:
        notification.status = NotificationStatus.CANCELLED
        notification.last_error = "у человека нет телеграма"
        return False

    # Запись могли отменить уже после постановки напоминания
    if notification.booking_id is not None:
        booking = await session.get(Booking, notification.booking_id)
        if booking is None or booking.status not in (
            BookingStatus.CONFIRMED,
            BookingStatus.PENDING,
        ):
            notification.status = NotificationStatus.CANCELLED
            return False

    text = notifier.render(notification)
    if not text.strip():
        notification.status = NotificationStatus.CANCELLED
        return False

    notification.attempts += 1

    try:
        await notifier.push(bot, user.tg_id, text)
    except TelegramBadRequest as exc:
        # Админ пишет текст рассылки руками, и любая «<» или незакрытый
        # тег ломают разбор HTML. Текст при этом нормальный — отправляем
        # его как есть, без разметки, вместо тихого провала.
        if "can't parse entities" not in str(exc).lower():
            notification.last_error = str(exc)[:500]
            notification.status = NotificationStatus.FAILED
            log.warning("Telegram отверг сообщение id=%s: %s", notification.id, exc)
            return False
        try:
            await notifier.push(bot, user.tg_id, text, parse_mode=None)
        except Exception as inner:  # noqa: BLE001
            notification.last_error = str(inner)[:500]
            notification.status = NotificationStatus.FAILED
            return False
        log.info("Сообщение id=%s отправлено без разметки", notification.id)
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(tz=settings.tz)
        return True
    except TelegramRetryAfter as exc:
        # Telegram просит подождать — не считаем это провалом
        log.warning("Лимит Telegram, пауза %s с", exc.retry_after)
        notification.attempts -= 1
        await asyncio.sleep(exc.retry_after)
        return False
    except TelegramForbiddenError:
        # Пользователь заблокировал бота — повторять бессмысленно
        log.info("Бот заблокирован у tg_id=%s", user.tg_id)
        notification.status = NotificationStatus.CANCELLED
        notification.last_error = "бот заблокирован пользователем"
        return False
    except Exception as exc:  # noqa: BLE001
        notification.last_error = str(exc)[:500]
        if notification.attempts >= MAX_ATTEMPTS:
            notification.status = NotificationStatus.FAILED
            log.warning(
                "Уведомление id=%s провалено после %d попыток",
                notification.id,
                notification.attempts,
            )
        return False

    notification.status = NotificationStatus.SENT
    notification.sent_at = datetime.now(tz=settings.tz)
    return True


async def process_queue(bot: Bot) -> tuple[int, int]:
    """Один проход по очереди. Возвращает (отправлено, не отправлено)."""
    sent = failed = 0

    async with session_factory() as session:
        try:
            pending = await notifier.due_notifications(session)
            for notification in pending:
                if await _send_one(bot, session, notification):
                    sent += 1
                else:
                    failed += 1
                await asyncio.sleep(SEND_DELAY)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    if sent or failed:
        log.info("Очередь: отправлено %d, не доставлено %d", sent, failed)
    return sent, failed


async def flush_now(bot: Bot, *, report_to: int | None = None) -> tuple[int, int]:
    """Разобрать очередь немедленно, не дожидаясь следующего тика.

    Вызывается после рассылки: админ только что нажал «Отправить»
    и ждёт результата, а не минуту тишины.
    """
    sent = failed = 0
    for _ in range(20):  # предел на случай, если очередь не пустеет
        batch_sent, batch_failed = await process_queue(bot)
        sent += batch_sent
        failed += batch_failed
        if batch_sent + batch_failed == 0:
            break

    if report_to is not None:
        parts = [f"📢 Рассылка завершена.\nДоставлено: {sent}"]
        if failed:
            parts.append(
                f"Не доставлено: {failed} — скорее всего, эти люди "
                f"заблокировали бота."
            )
        try:
            await notifier.push(bot, report_to, "\n".join(parts))
        except Exception:  # noqa: BLE001
            log.warning("Не удалось отправить отчёт о рассылке")

    return sent, failed


async def _mark_finished_bookings() -> int:
    """Помечает завершёнными выезды, которые давно закончились.

    Без этого статистика «сколько раз приезжал» не наполнялась бы,
    пока админ вручную не закроет каждую запись.
    """
    now = datetime.now(tz=settings.tz)
    async with session_factory() as session:
        result = await session.execute(
            update(Booking)
            .where(
                Booking.status == BookingStatus.CONFIRMED,
                Booking.ends_at < now,
            )
            .values(status=BookingStatus.COMPLETED)
        )
        await session.commit()
        count = result.rowcount or 0
    if count:
        log.info("Автоматически завершено выездов: %d", count)
    return count


async def run_worker(bot: Bot) -> None:
    """Бесконечный цикл. Запускается фоновой задачей из main."""
    log.info("Воркер уведомлений запущен, интервал %d с", TICK_SECONDS)
    ticks = 0

    while True:
        try:
            await process_queue(bot)
            # Завершённые выезды проверяем раз в час, чаще незачем
            ticks += 1
            if ticks % 60 == 0:
                await _mark_finished_bookings()
        except asyncio.CancelledError:
            log.info("Воркер уведомлений остановлен")
            raise
        except Exception:
            # Любая ошибка не должна убивать цикл: иначе одна
            # кривая строка в очереди остановит все напоминания
            log.exception("Ошибка в цикле воркера, продолжаю")

        await asyncio.sleep(TICK_SECONDS)
