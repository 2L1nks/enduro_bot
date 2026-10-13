"""Информационный раздел и рассылки."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin
from app.db.models.enums import NotificationKind, NotificationStatus
from app.db.models.types import pg_enum

if TYPE_CHECKING:
    from app.db.models.user import User


class InfoContent(IdMixin, TimestampMixin, Base):
    """Раздел «О прокате». Админ редактирует прямо из бота."""

    __tablename__ = "info_content"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # [{"type": "photo"|"video", "file_id": "...", "caption": "..."}]
    media: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    updated_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class Broadcast(IdMixin, TimestampMixin, Base):
    """Ручная рассылка от админа."""

    __tablename__ = "broadcasts"

    author_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # {"type": "all"|"booking"|"user", "booking_id": 1, "user_ids": [...]}
    audience: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    media: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="broadcast", cascade="all, delete-orphan"
    )


class Notification(IdMixin, TimestampMixin, Base):
    """Единица исходящего сообщения — очередь в БД.

    APScheduler сам по себе ненадёжен: при падении процесса задачи в памяти
    теряются. Поэтому напоминания материализуются строками здесь, а воркер
    раз в минуту забирает всё, у чего send_at уже наступил. Рестарт ничего
    не теряет, повторная отправка исключена статусом.
    """

    __tablename__ = "notifications"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    booking_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE")
    )
    broadcast_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE")
    )

    kind: Mapped[NotificationKind] = mapped_column(
        pg_enum(NotificationKind, "notification_kind"), nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        pg_enum(NotificationStatus, "notification_status"),
        nullable=False,
        default=NotificationStatus.SCHEDULED,
    )
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    broadcast: Mapped[Optional["Broadcast"]] = relationship(back_populates="notifications")
    user: Mapped["User"] = relationship()

    __table_args__ = (
        # Главный индекс воркера: «что пора отправить»
        Index("ix_notifications_due", "status", "send_at"),
        Index("ix_notifications_booking_kind", "booking_id", "kind"),
    )
