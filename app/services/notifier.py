"""Очередь уведомлений.

Напоминания не висят в памяти планировщика, а материализуются строками
в таблице notifications. Воркер раз в минуту забирает всё, у чего
наступило send_at. Поэтому рестарт бота ничего не теряет, а повторная
отправка исключена статусом строки.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Booking,
    Notification,
    NotificationKind,
    NotificationStatus,
    User,
    UserRole,
)
from app.utils.chat import remember_notice
from app.utils.logger import get_logger

log = get_logger(__name__)


async def push(bot, tg_id: int | None, text: str, **kwargs) -> int | None:
    """Уведомление человеку, которое само уберётся.

    Все сообщения «без спроса» идут только через эту функцию:
    подтверждение, напоминание, перенос, рассылка. Раньше каждое
    место слало своё сообщение напрямую, и они копились в чате —
    уходя из раздела, человек оставлял их висеть навсегда.
    Теперь уведомление живёт до следующего нажатия.

    Ошибки Telegram не глушатся: вызывающий решает, что с ними делать.
    Человеку без телеграма (аккаунт по ФИО) писать некуда — None.
    """
    if tg_id is None:
        return None
    message = await bot.send_message(tg_id, text, **kwargs)
    remember_notice(tg_id, message.message_id)
    return message.message_id

#: За сколько до выезда напоминать
REMINDER_OFFSETS = {
    NotificationKind.REMINDER_24H: timedelta(hours=24),
    NotificationKind.REMINDER_2H: timedelta(hours=2),
}


async def schedule_booking_reminders(
    session: AsyncSession, booking: Booking
) -> int:
    """Ставит напоминания всем участникам подтверждённой записи.

    Идемпотентно: при повторном вызове старые запланированные строки
    этой записи снимаются и создаются заново. Это важно при переносе
    выезда — иначе клиент получил бы напоминание на старое время.
    """
    await cancel_booking_reminders(session, booking.id)

    now = datetime.now(tz=settings.tz)
    created = 0

    for kind, offset in REMINDER_OFFSETS.items():
        send_at = booking.starts_at - offset
        # Поздно подтверждённую запись не напоминаем задним числом
        if send_at <= now:
            continue

        for participant in booking.participants:
            if participant.user_id is None:
                continue
            if participant.user is not None and not participant.user.has_telegram:
                continue  # аккаунт по ФИО — напоминание некуда отправить
            session.add(
                Notification(
                    user_id=participant.user_id,
                    booking_id=booking.id,
                    kind=kind,
                    status=NotificationStatus.SCHEDULED,
                    send_at=send_at,
                    payload={
                        "starts_at": booking.starts_at.isoformat(),
                        "meeting_point": booking.meeting_point or "",
                        "motorcycle": (
                            participant.motorcycle.title
                            if participant.motorcycle
                            else ""
                        ),
                    },
                )
            )
            created += 1

    await session.flush()
    log.info("Запись id=%s: запланировано напоминаний %d", booking.id, created)
    return created


async def cancel_booking_reminders(session: AsyncSession, booking_id: int) -> int:
    """Снимает неотправленные напоминания записи.

    Уже отправленные не трогаем: их статус — факт истории.
    """
    result = await session.execute(
        update(Notification)
        .where(
            Notification.booking_id == booking_id,
            Notification.status == NotificationStatus.SCHEDULED,
        )
        .values(status=NotificationStatus.CANCELLED)
    )
    await session.flush()
    return result.rowcount or 0


async def queue_broadcast(
    session: AsyncSession,
    recipients: list[User],
    text: str,
    *,
    broadcast_id: int | None = None,
) -> int:
    """Кладёт рассылку в очередь.

    Отправкой займётся воркер: при сотне получателей синхронная
    рассылка упёрлась бы в лимиты Telegram и подвесила бы хендлер.
    """
    now = datetime.now(tz=settings.tz)
    recipients = [user for user in recipients if user.has_telegram]
    for user in recipients:
        session.add(
            Notification(
                user_id=user.id,
                broadcast_id=broadcast_id,
                kind=NotificationKind.MANUAL,
                status=NotificationStatus.SCHEDULED,
                send_at=now,
                payload={"text": text},
            )
        )
    await session.flush()
    log.info("В очередь поставлена рассылка на %d получателей", len(recipients))
    return len(recipients)


#: Сообщения о заявках, которые ждут захода админа в панель.
#: tg_id -> список id сообщений. Живёт в памяти процесса: при
#: перезапуске уведомления просто останутся в чате, это не страшно.
PENDING_ALERTS: dict[int, list[int]] = {}


async def notify_staff(
    bot, session: AsyncSession, text: str, *, keep_until_admin: bool = False
) -> int:
    """Сообщение всем администраторам и владельцу.

    Нужно для событий, требующих реакции человека: новая заявка,
    отмена клиентом. Без этого админу пришлось бы заглядывать
    в раздел «Записи» наугад.
    """
    staff = await session.scalars(
        select(User).where(
            User.role.in_([UserRole.ADMIN, UserRole.OWNER]),
            User.is_blocked.is_(False),
        )
    )
    sent = 0
    for person in staff.all():
        try:
            if keep_until_admin:
                # Заявка должна висеть, пока админ не откроет панель —
                # обычное нажатие в клиентском меню её не убирает
                message = await bot.send_message(person.tg_id, text)
                PENDING_ALERTS.setdefault(person.tg_id, []).append(
                    message.message_id
                )
            else:
                await push(bot, person.tg_id, text)
            sent += 1
        except Exception as exc:  # noqa: BLE001 — один недоступный чат не важен
            log.warning("Не удалось уведомить админа %s: %s", person.tg_id, exc)
    return sent


async def clear_pending_alerts(bot, tg_id: int) -> int:
    """Убирает накопившиеся уведомления о заявках.

    Вызывается при заходе в админку: админ открыл панель — значит
    увидел, и сообщения в чате больше не нужны.
    """
    ids = PENDING_ALERTS.pop(tg_id, [])
    for message_id in ids:
        try:
            await bot.delete_message(tg_id, message_id)
        except Exception:  # noqa: BLE001
            pass
    return len(ids)


async def due_notifications(
    session: AsyncSession, limit: int = 50
) -> list[Notification]:
    """Что пора отправить прямо сейчас.

    SKIP LOCKED нужен на случай нескольких воркеров: каждый берёт
    свою пачку, никто не ждёт чужую блокировку и строки не дублируются.
    """
    now = datetime.now(tz=settings.tz)
    rows = await session.scalars(
        select(Notification)
        .where(
            Notification.status == NotificationStatus.SCHEDULED,
            Notification.send_at <= now,
            Notification.attempts < 3,
        )
        .order_by(Notification.send_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(rows.all())


def render(notification: Notification) -> str:
    """Текст сообщения по типу уведомления."""
    payload = notification.payload or {}

    if notification.kind == NotificationKind.MANUAL:
        return payload.get("text", "")

    starts_raw = payload.get("starts_at")
    when = ""
    if starts_raw:
        try:
            when = datetime.fromisoformat(starts_raw).strftime("%d.%m в %H:%M")
        except ValueError:
            when = starts_raw

    if notification.kind == NotificationKind.REMINDER_24H:
        head = f"🔔 Напоминание: завтра выезд, {when}."
    elif notification.kind == NotificationKind.REMINDER_2H:
        head = f"⏰ Выезд уже через два часа — {when}."
    else:
        head = f"Напоминание о выезде {when}."

    lines = [head]
    if payload.get("meeting_point"):
        lines.append(f"\n📍 Место сбора: {payload['meeting_point']}")
    if payload.get("motorcycle"):
        lines.append(f"🏍 Ваш мотоцикл: {payload['motorcycle']}")

    lines.append(
        "\n<b>Что взять с собой</b>\n"
        "• Паспорт или права\n"
        "• Удобную одежду по погоде\n"
        "• Воду и перекус\n"
        "Экипировку выдаём на месте."
    )
    return "\n".join(lines)
