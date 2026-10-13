"""Оплата на месте.

Деньги берут за каждого человека в группе, но платит обычно один —
ответственный за группу. У товарищей может вообще не быть телеграма,
поэтому платёж нельзя привязывать «один к человеку»: иначе за группу
из трёх запишется одна оплата вместо трёх, и выручка потеряется.

Модель такая: каждое место в группе — отдельная строка в payments.
Строка привязана к тому, кто за это место платит: к самому райдеру,
если у него есть аккаунт, иначе к ответственному за группу. Если
аккаунта нет ни у кого (группу завёл админ с улицы), строка пишется
без клиента — выручка всё равно попадёт в статистику.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Booking, BookingParticipant, Payment, PaymentStatus


@dataclass(slots=True)
class Seat:
    """Место в группе и его оплата."""

    participant: BookingParticipant
    payer_id: int | None
    paid: bool


@dataclass(slots=True)
class PaymentState:
    """Кто заплатил, сколько осталось."""

    seats: list[Seat]
    price: Decimal

    @property
    def total(self) -> Decimal:
        return self.price * len(self.seats)

    @property
    def paid_seats(self) -> int:
        return sum(1 for s in self.seats if s.paid)

    @property
    def unpaid_seats(self) -> int:
        return len(self.seats) - self.paid_seats

    @property
    def due(self) -> Decimal:
        """Сколько ещё не получено."""
        return self.price * self.unpaid_seats

    @property
    def settled(self) -> bool:
        return self.unpaid_seats == 0


def _pays_for_self(participant: BookingParticipant) -> bool:
    """Платит сам за себя только человек с телеграмом.

    У товарища без телеграма теперь тоже есть аккаунт (по ФИО), но
    деньги за него по-прежнему отдаёт ответственный — так и считаем.
    """
    return bool(
        participant.user_id
        and participant.user is not None
        and participant.user.has_telegram
    )


def _leader_id(booking: Booking) -> int | None:
    """Аккаунт ответственного за группу — на него ложатся чужие места.

    Ответственный может быть и без телеграма (группу с улицы внёс
    админ) — тогда оплата записывается на его аккаунт по ФИО, и такой
    постоянный клиент тоже виден в статистике.
    """
    leader = next(
        (p for p in booking.participants if p.is_leader and p.user_id), None
    )
    if leader:
        return leader.user_id
    # Отметка ответственного могла не сохраниться (старые записи) —
    # тогда платит первый, у кого вообще есть аккаунт
    return next((p.user_id for p in booking.participants if p.user_id), None)


def _by_payer(
    booking: Booking,
) -> dict[int | None, list[BookingParticipant]]:
    """Места, сгруппированные по тому, кто за них платит."""
    leader_id = _leader_id(booking)
    groups: dict[int | None, list[BookingParticipant]] = {}
    for participant in booking.participants:
        payer = participant.user_id if _pays_for_self(participant) else leader_id
        groups.setdefault(payer, []).append(participant)
    return groups


async def _counts(session: AsyncSession, booking_id: int) -> dict[int | None, int]:
    """Сколько мест уже оплачено каждым плательщиком."""
    rows = await session.execute(
        select(Payment.user_id, func.count())
        .where(
            Payment.booking_id == booking_id,
            Payment.status == PaymentStatus.PAID,
        )
        .group_by(Payment.user_id)
    )
    return {user_id: count for user_id, count in rows}


async def state(session: AsyncSession, booking: Booking) -> PaymentState:
    """Разложить оплату по местам.

    Платежи обезличены — к какому именно спутнику относится строка,
    в базе не записано. Поэтому закрываем сначала место самого
    плательщика, потом его спутников: так админ видит осмысленный
    порядок, а не случайные галочки.
    """
    counts = await _counts(session, booking.id)
    seats: list[Seat] = []

    for payer, members in _by_payer(booking).items():
        covered = counts.get(payer, 0)
        members = sorted(members, key=lambda p: p.user_id != payer)
        for index, participant in enumerate(members):
            seats.append(
                Seat(
                    participant=participant,
                    payer_id=payer,
                    paid=index < covered,
                )
            )

    # Порядок как в записи: ответственный первым
    order = {p.id: i for i, p in enumerate(booking.participants)}
    seats.sort(key=lambda s: order.get(s.participant.id, 0))
    return PaymentState(seats=seats, price=settings.price_per_person)


async def mark_all(
    session: AsyncSession, booking: Booking, *, actor_id: int | None
) -> Decimal:
    """Отметить оплату за все неоплаченные места.

    Повторное нажатие ничего не задваивает: дописываются только
    недостающие строки.
    """
    counts = await _counts(session, booking.id)
    now = datetime.now(tz=settings.tz)
    price = settings.price_per_person
    added = Decimal("0")

    for payer, members in _by_payer(booking).items():
        missing = len(members) - counts.get(payer, 0)
        for _ in range(max(0, missing)):
            session.add(
                Payment(
                    booking_id=booking.id,
                    user_id=payer,
                    amount=price,
                    method="cash",
                    status=PaymentStatus.PAID,
                    paid_at=now,
                    recorded_by_id=actor_id,
                )
            )
            added += price

    await session.flush()
    return added
