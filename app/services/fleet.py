"""Управление парком: карточки техники и плановые простои."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Booking,
    BookingParticipant,
    Experience,
    MaintenancePeriod,
    Motorcycle,
    MotorcycleStatus,
)
from app.exceptions import DomainError, ValidationError
from app.repositories import availability as avail_repo
from app.repositories import motorcycle as moto_repo
from app.utils.formatters import local
from app.utils.logger import get_logger
from app.utils.timeslots import TimeWindow

log = get_logger(__name__)


@dataclass(slots=True)
class AffectedBooking:
    """Запись, которую задевает вывод техники на обслуживание."""

    booking: Booking
    participant: BookingParticipant | None

    def describe(self) -> str:
        who = "участник не назначен"
        if self.participant and self.participant.user:
            who = self.participant.user.full_name or "без имени"
        return f"{local(self.booking.starts_at):%d.%m %H:%M} — {who}"


async def create(
    session: AsyncSession,
    *,
    title: str,
    model: str = "",
    reg_number: str | None = None,
    category: Experience = Experience.BEGINNER,
    engine_cc: int | None = None,
    seat_height_mm: int | None = None,
    min_rider_height_cm: int | None = None,
    max_rider_height_cm: int | None = None,
    max_rider_weight_kg: int | None = None,
    notes: str | None = None,
    actor_id: int | None = None,
) -> Motorcycle:
    if not title.strip():
        raise ValidationError(user_message="Название мотоцикла не может быть пустым.")
    if (
        min_rider_height_cm
        and max_rider_height_cm
        and min_rider_height_cm > max_rider_height_cm
    ):
        raise ValidationError(
            user_message="Минимальный рост не может быть больше максимального."
        )

    moto = Motorcycle(
        title=title.strip(),
        model=model.strip(),
        reg_number=(reg_number or "").strip() or None,
        category=category,
        engine_cc=engine_cc,
        seat_height_mm=seat_height_mm,
        min_rider_height_cm=min_rider_height_cm,
        max_rider_height_cm=max_rider_height_cm,
        max_rider_weight_kg=max_rider_weight_kg,
        notes=notes,
    )
    session.add(moto)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if "reg_number" in str(exc.orig):
            raise ValidationError(
                user_message=f"Мотоцикл с номером {reg_number} уже заведён."
            ) from exc
        raise

    session.add(
        AuditLog(actor_id=actor_id, action="create", entity="motorcycle", entity_id=moto.id)
    )
    log.info("Добавлен мотоцикл id=%s %r", moto.id, moto.title)
    return moto


async def set_status(
    session: AsyncSession,
    motorcycle_id: int,
    status: MotorcycleStatus,
    *,
    actor_id: int | None = None,
) -> Motorcycle:
    """Смена текущего статуса.

    Это именно «сейчас»: будущие простои заводятся периодами
    через schedule_maintenance, иначе календарь на месяц вперёд
    посчитать нельзя.
    """
    moto = await moto_repo.get(session, motorcycle_id)
    if moto is None:
        raise DomainError(user_message="Мотоцикл не найден.")

    old = moto.status
    moto.status = status
    # Возврат в строй снимает и флаг «выведен»: иначе техника осталась бы
    # невидимой для календаря, хотя статус показывает «доступен»
    if status == MotorcycleStatus.AVAILABLE and not moto.is_active:
        moto.is_active = True
        log.info("Мотоцикл id=%s возвращён в парк сменой статуса", motorcycle_id)
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="status",
            entity="motorcycle",
            entity_id=motorcycle_id,
            details=f"{old.value} -> {status.value}",
        )
    )
    log.info("Мотоцикл id=%s: статус %s -> %s", motorcycle_id, old.value, status.value)
    return moto


async def retire(
    session: AsyncSession, motorcycle_id: int, *, actor_id: int | None = None
) -> Motorcycle:
    """Вывод из парка.

    Физически не удаляем никогда: на мотоцикл ссылаются прошлые
    выезды и статистика. Снимаем is_active — из подбора он пропадает,
    история остаётся целой.
    """
    moto = await moto_repo.get(session, motorcycle_id)
    if moto is None:
        raise DomainError(user_message="Мотоцикл не найден.")

    moto.is_active = False
    moto.status = MotorcycleStatus.RETIRED
    session.add(
        AuditLog(actor_id=actor_id, action="retire", entity="motorcycle", entity_id=motorcycle_id)
    )
    log.info("Мотоцикл id=%s выведен из парка", motorcycle_id)
    return moto


async def restore(
    session: AsyncSession, motorcycle_id: int, *, actor_id: int | None = None
) -> Motorcycle:
    """Возврат техники в парк после вывода.

    Без этого выведенный мотоцикл оставался бы недоступен навсегда:
    статус поменять можно, а флаг is_active — нет.
    """
    moto = await moto_repo.get(session, motorcycle_id)
    if moto is None:
        raise DomainError(user_message="Мотоцикл не найден.")

    moto.is_active = True
    moto.status = MotorcycleStatus.AVAILABLE
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="restore",
            entity="motorcycle",
            entity_id=motorcycle_id,
        )
    )
    log.info("Мотоцикл id=%s возвращён в парк", motorcycle_id)
    return moto


async def delete_forever(
    session: AsyncSession, motorcycle_id: int, *, actor_id: int | None = None
) -> bool:
    """Физическое удаление — только для техники без истории выездов.

    Нужно, чтобы убрать ошибочно заведённый мотоцикл, не засоряя парк
    «выведенными». Если история есть, возвращаем False: такую технику
    удалять нельзя, иначе развалятся прошлые записи и статистика.
    """
    if await moto_repo.has_history(session, motorcycle_id):
        return False

    moto = await moto_repo.get(session, motorcycle_id)
    if moto is None:
        raise DomainError(user_message="Мотоцикл не найден.")

    for period in await moto_repo.maintenance_periods(session, motorcycle_id):
        await session.delete(period)

    await session.delete(moto)
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="delete",
            entity="motorcycle",
            entity_id=motorcycle_id,
        )
    )
    log.info("Мотоцикл id=%s удалён полностью", motorcycle_id)
    return True


async def check_maintenance_conflicts(
    session: AsyncSession, motorcycle_id: int, window: TimeWindow
) -> list[AffectedBooking]:
    """Какие записи пострадают, если вывести технику на этот период.

    Вызывается ДО фактического вывода: админ должен увидеть список
    и решить, что делать, а не обнаружить проблему постфактум.
    """
    bookings = await avail_repo.bookings_overlapping(session, motorcycle_id, window)

    affected: list[AffectedBooking] = []
    for booking in bookings:
        participant = next(
            (p for p in booking.participants if p.motorcycle_id == motorcycle_id), None
        )
        affected.append(AffectedBooking(booking, participant))
    return affected


async def schedule_maintenance(
    session: AsyncSession,
    motorcycle_id: int,
    window: TimeWindow,
    reason: str,
    *,
    actor_id: int | None = None,
    force: bool = False,
) -> tuple[MaintenancePeriod | None, list[AffectedBooking]]:
    """Ставит технику на обслуживание.

    Возвращает (период, затронутые записи).

    Если конфликты есть и force=False — период НЕ создаётся, возвращается
    (None, список). Админ смотрит, кого задевает, и решает: перенести
    записи, заменить технику участникам или всё же продавить решение.
    С force=True период создаётся несмотря на конфликты — существующие
    резервы при этом остаются, их надо разруливать вручную.
    """
    if window.end <= window.start:
        raise ValidationError(
            user_message="Дата окончания обслуживания должна быть позже начала."
        )

    conflicts = await check_maintenance_conflicts(session, motorcycle_id, window)
    if conflicts and not force:
        return None, conflicts

    try:
        period = await moto_repo.add_maintenance(
            session,
            motorcycle_id,
            Range(window.start, window.end, bounds="[)"),
            reason,
            actor_id,
        )
    except IntegrityError as exc:
        await session.rollback()
        if "ex_maintenance_no_overlap" in str(exc.orig):
            raise ValidationError(
                user_message=(
                    "На этот период обслуживание уже запланировано. "
                    "Проверьте график техники."
                )
            ) from exc
        raise

    session.add(
        AuditLog(
            actor_id=actor_id,
            action="maintenance",
            entity="motorcycle",
            entity_id=motorcycle_id,
            details=f"{window} | {reason} | конфликтов: {len(conflicts)}",
        )
    )
    log.info(
        "Мотоцикл id=%s на обслуживании %s (конфликтов: %d)",
        motorcycle_id,
        window,
        len(conflicts),
    )
    return period, conflicts


async def cancel_maintenance(
    session: AsyncSession, period_id: int, *, actor_id: int | None = None
) -> None:
    period = await session.get(MaintenancePeriod, period_id)
    if period is None:
        raise DomainError(user_message="Период обслуживания не найден.")

    await session.delete(period)
    session.add(
        AuditLog(
            actor_id=actor_id,
            action="maintenance_cancel",
            entity="motorcycle",
            entity_id=period.motorcycle_id,
        )
    )
    log.info("Снято обслуживание id=%s", period_id)
