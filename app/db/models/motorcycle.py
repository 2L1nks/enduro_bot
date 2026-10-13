"""Парк техники и плановые простои."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ExcludeConstraint, TSTZRANGE, Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin
from app.db.models.enums import Experience, MotorcycleStatus
from app.db.models.types import pg_enum

if TYPE_CHECKING:
    from app.db.models.booking import BookingParticipant, MotorcycleReservation
    from app.db.models.user import User


class Motorcycle(IdMixin, TimestampMixin, Base):
    """Единица техники. Каждый мотоцикл — отдельная сущность,
    доступность считается поштучно."""

    __tablename__ = "motorcycles"

    title: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reg_number: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    engine_cc: Mapped[Optional[int]] = mapped_column()

    # Категория по уровню райдера: под кого эта техника
    category: Mapped[Experience] = mapped_column(
        pg_enum(Experience, "experience_level"),
        nullable=False,
        default=Experience.BEGINNER,
    )

    # Ограничения подбора под участника. NULL = не ограничено.
    seat_height_mm: Mapped[Optional[int]] = mapped_column()
    min_rider_height_cm: Mapped[Optional[int]] = mapped_column()
    max_rider_height_cm: Mapped[Optional[int]] = mapped_column()
    max_rider_weight_kg: Mapped[Optional[int]] = mapped_column()

    status: Mapped[MotorcycleStatus] = mapped_column(
        pg_enum(MotorcycleStatus, "motorcycle_status"),
        nullable=False,
        default=MotorcycleStatus.AVAILABLE,
    )
    # Мягкое удаление: мотоцикл с историей выездов нельзя стереть физически
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    photo_s3_key: Mapped[Optional[str]] = mapped_column(String(512))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    maintenance_periods: Mapped[list["MaintenancePeriod"]] = relationship(
        back_populates="motorcycle", cascade="all, delete-orphan"
    )
    reservations: Mapped[list["MotorcycleReservation"]] = relationship(
        back_populates="motorcycle"
    )
    participants: Mapped[list["BookingParticipant"]] = relationship(
        back_populates="motorcycle"
    )

    __table_args__ = (
        CheckConstraint(
            "min_rider_height_cm IS NULL OR max_rider_height_cm IS NULL "
            "OR min_rider_height_cm <= max_rider_height_cm",
            name="ck_moto_height_range",
        ),
        Index("ix_moto_status_active", "status", "is_active"),
    )

    @property
    def label(self) -> str:
        reg = f" ({self.reg_number})" if self.reg_number else ""
        return f"{self.title}{reg}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Motorcycle id={self.id} {self.title!r} status={self.status.value}>"


class MaintenancePeriod(IdMixin, TimestampMixin, Base):
    """Плановый простой: ТО или ремонт на конкретный интервал.

    Именно интервал, а не глобальный статус — иначе календарь на будущее
    не посчитать. Пересечения между периодами одного мотоцикла запрещены
    на уровне БД.
    """

    __tablename__ = "maintenance_periods"

    motorcycle_id: Mapped[int] = mapped_column(
        ForeignKey("motorcycles.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[Range[datetime]] = mapped_column(TSTZRANGE, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    motorcycle: Mapped["Motorcycle"] = relationship(back_populates="maintenance_periods")
    created_by: Mapped[Optional["User"]] = relationship()

    __table_args__ = (
        ExcludeConstraint(
            ("motorcycle_id", "="),
            ("period", "&&"),
            name="ex_maintenance_no_overlap",
            using="gist",
        ),
        Index("ix_maintenance_period", "period", postgresql_using="gist"),
    )
