"""Доменные исключения.

У каждого есть user_message — текст, который можно показать человеку
без расшифровки. Технические подробности остаются в логах.
"""
from __future__ import annotations


class DomainError(Exception):
    """База для всех ошибок бизнес-логики."""

    user_message = "Что-то пошло не так. Попробуйте ещё раз."

    def __init__(self, message: str | None = None, *, user_message: str | None = None):
        super().__init__(message or self.__class__.__name__)
        if user_message:
            self.user_message = user_message


class SlotUnavailable(DomainError):
    """Свободной техники на это время не хватает."""

    user_message = "На это время уже нет свободных мотоциклов."

    def __init__(self, requested: int, available: int):
        self.requested = requested
        self.available = available
        if available == 0:
            msg = "На это время не осталось свободных мотоциклов."
        else:
            msg = (
                f"Нужно мотоциклов: {requested}, "
                f"а свободно только {available}. Выберите другое время "
                f"или уменьшите группу."
            )
        super().__init__(
            f"requested={requested} available={available}", user_message=msg
        )


class SlotJustTaken(DomainError):
    """Гонка: пока оформляли, технику занял кто-то другой."""

    user_message = (
        "Этот слот только что заняли. Пожалуйста, выберите другое время."
    )


class BookingNotFound(DomainError):
    user_message = "Запись не найдена."


class AccessDenied(DomainError):
    user_message = "У вас нет доступа к этому разделу."


class ValidationError(DomainError):
    """Некорректный ввод от пользователя."""
