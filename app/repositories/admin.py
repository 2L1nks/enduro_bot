"""Запросы админ-панели."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import (
    RU_BOOKING_STATUS,
    RU_EXPERIENCE,
    Booking,
    BookingParticipant,
    BookingStatus,
    Motorcycle,
    Payment,
    PaymentStatus,
    User,
    UserRole,
)
from app.repositories.booking import ACTIVE_STATUSES
from app.utils.formatters import local

_WITH_PARTICIPANTS = selectinload(Booking.participants).selectinload(
    BookingParticipant.user
)


#: Дни недели для фильтров админки. PostgreSQL: 0 = воскресенье.
_WEEKDAY_SETS = {
    "weekdays": (1, 2, 3, 4, 5),
    "saturday": (6,),
    "sunday": (0,),
}


async def bookings_by_scope(session: AsyncSession, scope: str) -> list[Booking]:
    """Записи по фильтру.

    Прокат живёт неделями: будни — редкие выезды, суббота и воскресенье
    расписаны плотно. Поэтому списки разделены по дням недели,
    а не по «сегодня/неделя».

    scope: weekdays | saturday | sunday | pending | all
    """
    now = datetime.now(tz=settings.tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    query = select(Booking).options(
        _WITH_PARTICIPANTS,
        selectinload(Booking.participants).selectinload(BookingParticipant.motorcycle),
    )

    if scope == "today":
        # Для сводки на главной админки, в фильтрах списка не показывается
        query = query.where(
            Booking.starts_at >= today_start,
            Booking.starts_at < today_start + timedelta(days=1),
        ).order_by(Booking.starts_at)
    elif scope in _WEEKDAY_SETS:
        # Время хранится в UTC, поэтому день недели считаем
        # в зоне проката, иначе поздний субботний выезд
        # попал бы в воскресенье
        local_start = func.timezone(settings.timezone, Booking.starts_at)
        query = query.where(
            Booking.starts_at >= today_start,
            func.extract("dow", local_start).in_(_WEEKDAY_SETS[scope]),
        ).order_by(Booking.starts_at)
    elif scope == "pending":
        query = query.where(Booking.status == BookingStatus.PENDING).order_by(
            Booking.starts_at
        )
    else:
        query = query.order_by(Booking.starts_at.desc()).limit(100)

    return list((await session.scalars(query)).unique().all())


# ---------- Календарь записей ----------


@dataclass(slots=True)
class DayMark:
    """Что происходит в день — для отметки на календаре."""

    pending: int = 0      # ждут подтверждения
    active: int = 0       # подтверждены
    done: int = 0         # прошли: завершены или неявка
    cancelled: int = 0

    @property
    def total(self) -> int:
        return self.pending + self.active + self.done + self.cancelled

    @property
    def marker(self) -> str:
        """Значок дня: сначала то, что требует действия."""
        if self.pending:
            return "🕓"
        if self.active:
            return "🟢"
        if self.done:
            return "🏁"
        if self.cancelled:
            return "✖"
        return ""


async def month_marks(
    session: AsyncSession, since: datetime, until: datetime
) -> dict[date, DayMark]:
    """Отметки дней месяца.

    День считается по часам проката: в базе время в UTC, и поздний
    выезд иначе попал бы на соседнюю дату. Записей за месяц —
    десятки, поэтому группируем в Python, а не в SQL.
    """
    rows = await session.execute(
        select(Booking.starts_at, Booking.status).where(
            Booking.starts_at >= since, Booking.starts_at < until
        )
    )
    marks: dict[date, DayMark] = {}
    for starts_at, status in rows:
        mark = marks.setdefault(local(starts_at).date(), DayMark())
        if status == BookingStatus.PENDING:
            mark.pending += 1
        elif status == BookingStatus.CONFIRMED:
            mark.active += 1
        elif status == BookingStatus.CANCELLED:
            mark.cancelled += 1
        else:
            mark.done += 1
    return marks


async def bookings_between(
    session: AsyncSession, since: datetime, until: datetime
) -> list[Booking]:
    """Записи за интервал — для экрана дня."""
    return list(
        (
            await session.scalars(
                select(Booking)
                .where(Booking.starts_at >= since, Booking.starts_at < until)
                .options(_WITH_PARTICIPANTS)
                .order_by(Booking.starts_at, Booking.id)
            )
        )
        .unique()
        .all()
    )


async def cancelled_for_purge(
    session: AsyncSession, since: datetime, until: datetime
) -> tuple[list[Booking], int]:
    """Отменённые записи, которые можно удалить, и сколько оставлено.

    Удаление записи каскадом стирает её оплаты. Отменённую запись
    обычно не оплачивают, но если деньги по ней всё же записаны —
    оставляем её: выручка в статистике не должна меняться задним числом.
    """
    rows = (
        await session.scalars(
            select(Booking)
            .where(
                Booking.starts_at >= since,
                Booking.starts_at < until,
                Booking.status == BookingStatus.CANCELLED,
            )
            .options(_WITH_PARTICIPANTS)
            .order_by(Booking.starts_at)
        )
    ).unique().all()

    paid_ids = set(
        (
            await session.scalars(
                select(Payment.booking_id)
                .where(Payment.booking_id.in_([b.id for b in rows] or [0]))
                .distinct()
            )
        ).all()
    )
    deletable = [b for b in rows if b.id not in paid_ids]
    return deletable, len(rows) - len(deletable)


async def delete_bookings(session: AsyncSession, booking_ids: list[int]) -> int:
    """Удалить записи насовсем. Участники и напоминания — каскадом.

    Повторно проверяем статус в самом запросе: между экраном
    подтверждения и нажатием запись могли восстановить.
    """
    if not booking_ids:
        return 0
    result = await session.execute(
        delete(Booking).where(
            Booking.id.in_(booking_ids),
            Booking.status == BookingStatus.CANCELLED,
            # payments.booking_id обязателен, поэтому NOT IN без ловушки с NULL
            Booking.id.not_in(select(Payment.booking_id)),
        )
    )
    await session.flush()
    return result.rowcount or 0


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    return await session.scalar(
        select(Booking)
        .where(Booking.id == booking_id)
        .options(
            _WITH_PARTICIPANTS,
            selectinload(Booking.participants).selectinload(
                BookingParticipant.motorcycle
            ),
            selectinload(Booking.reservations),
        )
    )


async def pending_count(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.status == BookingStatus.PENDING)
        )
        or 0
    )


async def search_users(
    session: AsyncSession, query: str, limit: int = 10, *, telegram_only: bool = False
) -> list[User]:
    """Поиск клиента по имени, нику или Telegram ID.

    telegram_only — для сотрудников, рассылки и фото: человеку без
    телеграма их не передать.
    """
    text = query.strip().lstrip("@")
    conditions = [
        User.full_name.ilike(f"%{text}%"),
        User.username.ilike(f"%{text}%"),
    ]
    if text.isdigit():
        conditions.append(User.tg_id == int(text))

    return list(
        (
            await session.scalars(
                select(User)
                .where(or_(*conditions), User.tg_id.is_not(None) if telegram_only else true())
                .order_by(User.full_name)
                .limit(limit)
            )
        ).all()
    )


async def staff_members(session: AsyncSession) -> list[User]:
    return list(
        (
            await session.scalars(
                select(User)
                .where(User.role != UserRole.USER)
                .order_by(User.role, User.full_name)
            )
        ).all()
    )


async def broadcast_audience(session: AsyncSession, target: str) -> list[User]:
    """Получатели рассылки.

    all — все незаблокированные клиенты,
    upcoming — только участники предстоящих подтверждённых выездов.
    """
    if target == "upcoming":
        now = datetime.now(tz=settings.tz)
        query = (
            select(User)
            .join(BookingParticipant, BookingParticipant.user_id == User.id)
            .join(Booking, Booking.id == BookingParticipant.booking_id)
            .where(
                Booking.starts_at >= now,
                Booking.status == BookingStatus.CONFIRMED,
                User.is_blocked.is_(False),
            )
            .distinct()
        )
    else:
        query = select(User).where(User.is_blocked.is_(False))

    # Аккаунтам по ФИО писать некуда
    query = query.where(User.tg_id.is_not(None))
    return list((await session.scalars(query)).all())


@dataclass(slots=True)
class MonthReport:
    """Сводка за календарный месяц для раздела «Статистика»."""

    rides_done: int        # проведено выездов
    riders_done: int       # человек на них — с телеграмом и без
    rides_ahead: int       # предстоит в этом месяце
    riders_ahead: int
    pending: int           # из предстоящих ждут подтверждения
    no_shows: int
    cancelled: int
    received: Decimal      # получено денег за месяц (по дате оплаты)
    debt: Decimal          # не доплачено по проведённым выездам месяца
    expected: Decimal      # ожидается с предстоящих выездов


async def month_report(
    session: AsyncSession, since: datetime, until: datetime, now: datetime
) -> MonthReport:
    """Сводка за месяц [since, until).

    Месяц берётся целиком, а не «до текущей минуты»: иначе все
    предстоящие выезды выпадали из отчёта, и в начале месяца экран
    состоял из нулей. Проведённое и предстоящее считаются раздельно.

    Люди считаются по местам в группе, а не по аккаунтам: группа
    с улицы без телеграма — такие же клиенты.
    """
    in_month = and_(Booking.starts_at >= since, Booking.starts_at < until)
    done = Booking.status == BookingStatus.COMPLETED
    ahead = and_(Booking.status.in_(ACTIVE_STATUSES), Booking.ends_at >= now)

    rides = (
        await session.execute(
            select(
                func.count().filter(done),
                func.count().filter(ahead),
                func.count().filter(
                    and_(Booking.status == BookingStatus.PENDING, Booking.ends_at >= now)
                ),
                func.count().filter(Booking.status == BookingStatus.NO_SHOW),
                func.count().filter(Booking.status == BookingStatus.CANCELLED),
                func.coalesce(func.sum(Booking.total_price).filter(ahead), 0),
            )
            .select_from(Booking)
            .where(in_month)
        )
    ).one()

    riders = (
        await session.execute(
            select(
                func.count(BookingParticipant.id).filter(done),
                func.count(BookingParticipant.id).filter(ahead),
            )
            .select_from(BookingParticipant)
            .join(Booking, Booking.id == BookingParticipant.booking_id)
            .where(in_month)
        )
    ).one()

    received = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.PAID,
            Payment.paid_at >= since,
            Payment.paid_at < until,
        )
    )

    # Долг считается по каждому выезду отдельно: сколько стоил минус
    # сколько за него заплатили. Вычитать месячные суммы друг из друга
    # нельзя — оплата за выезд прошлого месяца уводила итог в минус.
    paid_per_booking = (
        select(
            Payment.booking_id.label("booking_id"),
            func.sum(Payment.amount).label("paid"),
        )
        .where(Payment.status == PaymentStatus.PAID)
        .group_by(Payment.booking_id)
        .subquery()
    )
    debt = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.greatest(
                        Booking.total_price
                        - func.coalesce(paid_per_booking.c.paid, 0),
                        0,
                    )
                ),
                0,
            )
        )
        .select_from(Booking)
        .outerjoin(paid_per_booking, paid_per_booking.c.booking_id == Booking.id)
        .where(in_month, done)
    )

    return MonthReport(
        rides_done=rides[0] or 0,
        riders_done=riders[0] or 0,
        rides_ahead=rides[1] or 0,
        riders_ahead=riders[1] or 0,
        pending=rides[2] or 0,
        no_shows=rides[3] or 0,
        cancelled=rides[4] or 0,
        received=Decimal(received or 0),
        debt=Decimal(debt or 0),
        expected=Decimal(rides[5] or 0),
    )


async def top_clients(
    session: AsyncSession, limit: int = 10
) -> list[tuple[User, int, Decimal]]:
    """Постоянные клиенты: кто чаще ездит и больше платит."""
    # Оплаты без клиента (группа с улицы) в выручку идут, но в этот
    # список — нет: там некого показывать
    paid_sum = (
        select(Payment.user_id, func.sum(Payment.amount).label("total"))
        .where(
            Payment.status == PaymentStatus.PAID,
            Payment.user_id.is_not(None),
        )
        .group_by(Payment.user_id)
        .subquery()
    )

    rows = await session.execute(
        select(
            User,
            func.count(BookingParticipant.id).label("trips"),
            func.coalesce(paid_sum.c.total, 0).label("spent"),
        )
        .join(BookingParticipant, BookingParticipant.user_id == User.id)
        .join(Booking, Booking.id == BookingParticipant.booking_id)
        .outerjoin(paid_sum, paid_sum.c.user_id == User.id)
        .where(Booking.status == BookingStatus.COMPLETED)
        .group_by(User.id, paid_sum.c.total)
        .order_by(func.count(BookingParticipant.id).desc())
        .limit(limit)
    )
    return [(r[0], r[1], Decimal(r[2])) for r in rows]


async def export_rows(session: AsyncSession) -> list[dict]:
    """Плоская выгрузка «одна строка — один участник выезда».

    Участник без аккаунта тоже попадает в выгрузку — по имени из
    записи. Время — по часам проката, а не UTC из базы.
    """
    rows = await session.execute(
        select(Booking, BookingParticipant, User, Motorcycle)
        .join(BookingParticipant, BookingParticipant.booking_id == Booking.id)
        .outerjoin(User, User.id == BookingParticipant.user_id)
        .outerjoin(Motorcycle, Motorcycle.id == BookingParticipant.motorcycle_id)
        .order_by(Booking.starts_at.desc(), BookingParticipant.is_leader.desc())
    )

    result = []
    for booking, participant, user, moto in rows:
        starts = local(booking.starts_at)
        result.append(
            {
                "Запись": booking.id,
                "Дата": starts.strftime("%d.%m.%Y"),
                "Время": starts.strftime("%H:%M"),
                "Длительность, мин": booking.duration_minutes,
                "Статус": RU_BOOKING_STATUS[booking.status],
                "Клиент": (user.full_name if user else None) or participant.full_name or "",
                "Ответственный": "да" if participant.is_leader else "",
                "Username": (user.username if user else None) or participant.tg_username or "",
                "Telegram ID": user.tg_id if user and user.tg_id else "",
                "Телефон": (user.phone if user else None) or (
                    booking.contact_phone if participant.is_leader else None
                ) or "",
                "Рост": participant.height_cm or "",
                "Вес": participant.weight_kg or "",
                "Размер ноги": participant.shoe_size or "",
                "Опыт": RU_EXPERIENCE[participant.experience] if participant.experience else "",
                "Мотоцикл": moto.title if moto else "",
                "Стоимость": float(participant.price),
                "Место сбора": booking.meeting_point or "",
                "Комментарий": booking.comment or "",
            }
        )
    return result
