"""Выезды, участники, резервации техники, оплаты."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint, TSTZRANGE, Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin
from app.db.models.enums import BookingStatus, Experience, PaymentStatus
from app.db.models.types import pg_enum

if TYPE_CHECKING:
    from app.db.models.motorcycle import Motorcycle
    from app.db.models.photo import Photo
    from app.db.models.user import User


class Booking(IdMixin, TimestampMixin, Base):
    """Групповой выезд.

    PENDING-заявка технику не занимает. Резервации появляются только
    при подтверждении админом.
    """

    __tablename__ = "bookings"

    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[BookingStatus] = mapped_column(
        pg_enum(BookingStatus, "booking_status"),
        nullable=False,
        default=BookingStatus.PENDING,
    )

    requested_seats: Mapped[int] = mapped_column(nullable=False, default=1)
    meeting_point: Mapped[Optional[str]] = mapped_column(String(512))
    total_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0")
    )
    comment: Mapped[Optional[str]] = mapped_column(Text)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(512))
    # Телефон ответственного за группу — спрашиваем в конце записи
    contact_phone: Mapped[Optional[str]] = mapped_column(String(32))

    created_by: Mapped[Optional["User"]] = relationship(
        back_populates="created_bookings", foreign_keys=[created_by_id]
    )
    participants: Mapped[list["BookingParticipant"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", lazy="selectin"
    )
    reservations: Mapped[list["MotorcycleReservation"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )
    photos: Mapped[list["Photo"]] = relationship(back_populates="booking")

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_booking_time_order"),
        CheckConstraint("requested_seats > 0", name="ck_booking_seats_positive"),
        CheckConstraint("total_price >= 0", name="ck_booking_price_non_negative"),
        Index("ix_bookings_starts_at", "starts_at"),
        Index("ix_bookings_status_starts", "status", "starts_at"),
    )

    @property
    def duration_minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    @property
    def is_active(self) -> bool:
        return self.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Booking id={self.id} {self.starts_at:%d.%m %H:%M} {self.status.value}>"


class BookingParticipant(IdMixin, TimestampMixin, Base):
    """Участник выезда. Всегда привязан к пользователю с аккаунтом.

    Антропометрия копируется из профиля в момент записи: человек худеет,
    а историческая запись должна помнить, под кого подбирали технику.
    """

    __tablename__ = "booking_participants"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    # Необязателен: в группе может быть человек без телеграма.
    # Ответственный за группу — тот, кто записывался.
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    tg_username: Mapped[Optional[str]] = mapped_column(String(64))
    is_leader: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    motorcycle_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("motorcycles.id", ondelete="SET NULL")
    )

    height_cm: Mapped[Optional[int]] = mapped_column()
    weight_kg: Mapped[Optional[int]] = mapped_column()
    shoe_size: Mapped[Optional[int]] = mapped_column()
    experience: Mapped[Optional[Experience]] = mapped_column(
        pg_enum(Experience, "experience_level")
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0")
    )
    comment: Mapped[Optional[str]] = mapped_column(Text)

    booking: Mapped["Booking"] = relationship(back_populates="participants")
    user: Mapped[Optional["User"]] = relationship(
        back_populates="participations", foreign_keys=[user_id], lazy="joined"
    )
    motorcycle: Mapped[Optional["Motorcycle"]] = relationship(
        back_populates="participants", lazy="joined"
    )

    __table_args__ = (
        CheckConstraint(
            "height_cm IS NULL OR (height_cm BETWEEN 120 AND 230)", name="ck_part_height"
        ),
        CheckConstraint(
            "weight_kg IS NULL OR (weight_kg BETWEEN 30 AND 200)", name="ck_part_weight"
        ),
        CheckConstraint(
            "shoe_size IS NULL OR (shoe_size BETWEEN 33 AND 50)", name="ck_part_shoe"
        ),
        CheckConstraint("price >= 0", name="ck_part_price_non_negative"),
        Index("ix_participants_user", "user_id"),
        Index("ix_participants_leader", "booking_id", "is_leader"),
    )


class MotorcycleReservation(IdMixin, TimestampMixin, Base):
    """Центральная таблица доступности: мотоцикл занят на интервал.

    period включает буфер на осмотр и заправку, поэтому пересечение
    интервалов — это и есть конфликт. EXCLUDE-констрейнт делает двойное
    бронирование физически невозможным: при гонке двух транзакций за
    последним мотоциклом вторая упадёт на уровне БД.
    """

    __tablename__ = "motorcycle_reservations"

    motorcycle_id: Mapped[int] = mapped_column(
        ForeignKey("motorcycles.id", ondelete="RESTRICT"), nullable=False
    )
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("booking_participants.id", ondelete="CASCADE"), unique=True
    )
    period: Mapped[Range[datetime]] = mapped_column(TSTZRANGE, nullable=False)

    motorcycle: Mapped["Motorcycle"] = relationship(back_populates="reservations")
    booking: Mapped["Booking"] = relationship(back_populates="reservations")

    __table_args__ = (
        ExcludeConstraint(
            ("motorcycle_id", "="),
            ("period", "&&"),
            name="ex_reservation_no_overlap",
            using="gist",
        ),
        Index("ix_reservation_period", "period", postgresql_using="gist"),
        Index("ix_reservation_booking", "booking_id"),
    )


class Payment(IdMixin, TimestampMixin, Base):
    """Оплата на месте. Нужна для статистики «сколько клиент потратил»."""

    __tablename__ = "payments"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    # Платит обычно ответственный за группу. Пусто, если аккаунта нет
    # ни у кого: группу завёл админ с улицы, деньги получены, и выручка
    # должна попасть в статистику, даже если привязать её не к кому.
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="cash")
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"),
        nullable=False,
        default=PaymentStatus.UNPAID,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    recorded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    booking: Mapped["Booking"] = relationship(back_populates="payments")
    user: Mapped[Optional["User"]] = relationship(
        back_populates="payments", foreign_keys=[user_id]
    )

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payment_amount_non_negative"),
        Index("ix_payments_user_status", "user_id", "status"),
    )
