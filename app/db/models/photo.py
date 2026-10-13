"""Фотографии клиентов в объектном хранилище."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.booking import Booking
    from app.db.models.user import User


class Photo(IdMixin, TimestampMixin, Base):
    """Фото клиента.

    Наружу уходит не ссылка, а сам файл через бота: проверяем owner_id,
    потом отдаём по кешированному telegram_file_id. Перебор id бесполезен.
    s3_key никогда не показывается пользователю.
    """

    __tablename__ = "photos"

    s3_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    #: Путь на Яндекс.Диске, если используется он
    disk_path: Mapped[Optional[str]] = mapped_column(String(512))
    telegram_file_id: Mapped[Optional[str]] = mapped_column(String(256))
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    booking_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL")
    )
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False, default="image/jpeg")
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    caption: Mapped[Optional[str]] = mapped_column(String(512))

    owner: Mapped["User"] = relationship(back_populates="photos", foreign_keys=[owner_id])
    booking: Mapped[Optional["Booking"]] = relationship(back_populates="photos")

    __table_args__ = (Index("ix_photos_owner_created", "owner_id", "created_at"),)
