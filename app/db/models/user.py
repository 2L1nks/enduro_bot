"""Пользователи и персонал."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin
from app.db.models.enums import Experience, UserRole
from app.db.models.types import pg_enum

if TYPE_CHECKING:
    from app.db.models.booking import Booking, BookingParticipant, Payment
    from app.db.models.photo import Photo


class User(IdMixin, TimestampMixin, Base):
    """Клиент или сотрудник. Роль хранится прямо здесь — отдельная таблица
    admins избыточна, пока ролей четыре штуки."""

    __tablename__ = "users"

    # Пусто у человека без телеграма: его привёз клиент или внёс админ.
    # Такой аккаунт опознаётся по ФИО — см. name_key.
    tg_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    #: ФИО в нормальной форме, ключ аккаунта без телеграма.
    #: Уникальность среди таких аккаунтов держит индекс в базе.
    name_key: Mapped[Optional[str]] = mapped_column(String(255))
    username: Mapped[Optional[str]] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[Optional[str]] = mapped_column(String(32))

    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), nullable=False, default=UserRole.USER
    )

    # Антропометрия — значения по умолчанию для участия в выездах.
    # Персональные данные: показываются только владельцу и админу.
    height_cm: Mapped[Optional[int]] = mapped_column()
    weight_kg: Mapped[Optional[int]] = mapped_column()
    shoe_size: Mapped[Optional[int]] = mapped_column()
    experience: Mapped[Optional[Experience]] = mapped_column(
        pg_enum(Experience, "experience_level")
    )

    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Личная папка на Яндекс.Диске. Имя случайное, чтобы публичную
    # ссылку нельзя было подобрать перебором.
    disk_folder: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    disk_public_url: Mapped[Optional[str]] = mapped_column(String(512))
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    added_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    participations: Mapped[list["BookingParticipant"]] = relationship(
        back_populates="user", foreign_keys="BookingParticipant.user_id"
    )
    created_bookings: Mapped[list["Booking"]] = relationship(
        back_populates="created_by", foreign_keys="Booking.created_by_id"
    )
    # foreign_keys обязателен: в payments два ключа на users —
    # кто платил (user_id) и кто записал оплату (recorded_by_id)
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user", foreign_keys="Payment.user_id"
    )
    photos: Mapped[list["Photo"]] = relationship(
        back_populates="owner", foreign_keys="Photo.owner_id"
    )

    __table_args__ = (
        CheckConstraint(
            "height_cm IS NULL OR (height_cm BETWEEN 120 AND 230)", name="ck_users_height"
        ),
        CheckConstraint(
            "weight_kg IS NULL OR (weight_kg BETWEEN 30 AND 200)", name="ck_users_weight"
        ),
        CheckConstraint(
            "shoe_size IS NULL OR (shoe_size BETWEEN 33 AND 50)", name="ck_users_shoe"
        ),
        CheckConstraint(
            "tg_id IS NOT NULL OR name_key IS NOT NULL", name="ck_users_identity"
        ),
        Index("ix_users_role", "role"),
        Index("ix_users_full_name", "full_name"),
        Index(
            "uq_users_offline_name",
            "name_key",
            unique=True,
            postgresql_where=text("tg_id IS NULL"),
        ),
    )

    @property
    def has_telegram(self) -> bool:
        """Можно ли человеку написать: уведомления, рассылки, фото."""
        return self.tg_id is not None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} tg_id={self.tg_id} role={self.role.value}>"


class AuditLog(IdMixin, TimestampMixin, Base):
    """Кто что менял. Пригодится при спорах и при разборе, кто снял мотоцикл."""

    __tablename__ = "audit_log"

    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    details: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (Index("ix_audit_entity", "entity", "entity_id"),)
