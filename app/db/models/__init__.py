"""Все модели импортируются здесь, чтобы Alembic их видел."""
from app.db.base import Base
from app.db.models.booking import (
    Booking,
    BookingParticipant,
    MotorcycleReservation,
    Payment,
)
from app.db.models.content import Broadcast, InfoContent, Notification
from app.db.models.enums import (
    RU_BOOKING_STATUS,
    RU_EXPERIENCE,
    RU_MOTO_STATUS,
    RU_ROLE,
    BookingStatus,
    Experience,
    MotorcycleStatus,
    NotificationKind,
    NotificationStatus,
    PaymentStatus,
    UserRole,
)
from app.db.models.motorcycle import MaintenancePeriod, Motorcycle
from app.db.models.photo import Photo
from app.db.models.user import AuditLog, User

__all__ = [
    "Base",
    "User",
    "AuditLog",
    "Motorcycle",
    "MaintenancePeriod",
    "Booking",
    "BookingParticipant",
    "MotorcycleReservation",
    "Payment",
    "Photo",
    "InfoContent",
    "Broadcast",
    "Notification",
    "UserRole",
    "Experience",
    "MotorcycleStatus",
    "BookingStatus",
    "PaymentStatus",
    "NotificationKind",
    "NotificationStatus",
    # Русские подписи для интерфейса
    "RU_ROLE",
    "RU_EXPERIENCE",
    "RU_MOTO_STATUS",
    "RU_BOOKING_STATUS",
]
