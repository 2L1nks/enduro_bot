"""Разбор и проверка пользовательского ввода.

Каждая функция либо возвращает готовое значение, либо бросает
ValidationError с понятным человеку текстом. Пределы совпадают
с CHECK-ограничениями в БД: нельзя, чтобы бот принял значение,
которое потом отвергнет база.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from app.config import settings
from app.exceptions import ValidationError

HEIGHT_RANGE = (120, 230)
WEIGHT_RANGE = (30, 200)
SHOE_RANGE = (33, 50)
ENGINE_RANGE = (50, 1500)

_DATE_PATTERNS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")
_DATETIME_PATTERNS = (
    "%d.%m.%Y %H:%M",
    "%d.%m.%y %H:%M",
    "%Y-%m-%d %H:%M",
    "%d.%m %H:%M",
)


def _int_in_range(raw: str, lo: int, hi: int, what: str, unit: str) -> int:
    text = raw.strip().replace(",", ".")
    # «178 см» и «178см» — частый ввод, цифры достаём сами
    digits = re.sub(r"[^\d]", "", text.split(".")[0])
    if not digits:
        raise ValidationError(user_message=f"{what} — введите число.")
    value = int(digits)
    if not lo <= value <= hi:
        suffix = f" {unit}" if unit else ""
        raise ValidationError(
            user_message=f"{what} — от {lo} до {hi}{suffix}. Вы ввели: {value}."
        )
    return value


def parse_height(raw: str) -> int:
    return _int_in_range(raw, *HEIGHT_RANGE, "Рост", "см")


def parse_weight(raw: str) -> int:
    return _int_in_range(raw, *WEIGHT_RANGE, "Вес", "кг")


def parse_shoe(raw: str) -> int:
    return _int_in_range(raw, *SHOE_RANGE, "Размер ноги", "")


def parse_date(raw: str) -> datetime:
    """Дата без времени. Год можно опустить — подставится текущий."""
    text = raw.strip()
    for pattern in _DATE_PATTERNS:
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=settings.tz)

    # Формат «10.11» без года
    try:
        parsed = datetime.strptime(text, "%d.%m")
    except ValueError as exc:
        raise ValidationError(
            user_message="Дата — в формате ДД.ММ.ГГГГ, например 15.11.2026."
        ) from exc
    return parsed.replace(year=datetime.now(tz=settings.tz).year, tzinfo=settings.tz)


def parse_datetime(raw: str) -> datetime:
    """Дата и время выезда."""
    text = raw.strip()
    now = datetime.now(tz=settings.tz)

    for pattern in _DATETIME_PATTERNS:
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        if "%Y" not in pattern and "%y" not in pattern:
            parsed = parsed.replace(year=now.year)
        return parsed.replace(tzinfo=settings.tz)

    raise ValidationError(
        user_message=(
            "Дата и время — в формате ДД.ММ.ГГГГ ЧЧ:ММ, например 15.11.2026 10:00."
        )
    )


def parse_time(raw: str) -> time:
    text = raw.strip().replace(".", ":").replace("-", ":")
    for pattern in ("%H:%M", "%H"):
        try:
            return datetime.strptime(text, pattern).time()
        except ValueError:
            continue
    raise ValidationError(user_message="Время — в формате ЧЧ:ММ, например 10:00.")


def parse_date_range(raw: str) -> tuple[datetime, datetime]:
    """Диапазон дат обслуживания: «12.11.2026 - 15.11.2026» или одна дата.

    Одна дата означает весь день целиком.
    """
    parts = re.split(r"\s*[-—–]\s*", raw.strip())
    if len(parts) == 1:
        start = parse_date(parts[0])
        return start, start + timedelta(days=1)
    if len(parts) != 2:
        raise ValidationError(
            user_message="Период — в формате ДД.ММ.ГГГГ - ДД.ММ.ГГГГ."
        )

    start, end = parse_date(parts[0]), parse_date(parts[1])
    # Конечная дата включительно: «12-14» значит по 14-е целиком
    end = end + timedelta(days=1)
    if end <= start:
        raise ValidationError(
            user_message="Дата окончания должна быть не раньше даты начала."
        )
    return start, end


def parse_duration(raw: str) -> int:
    """Длительность в минутах: «4ч», «4 часа», «240», «2.5ч»."""
    text = raw.strip().lower().replace(",", ".")
    match = re.match(
        r"^(\d+(?:\.\d+)?)\s*(ч|час|часа|часов|h|мин|минут|м|min)?$", text
    )
    if not match:
        raise ValidationError(
            user_message="Длительность — например «2.5ч», «90мин» или «90»."
        )
    value = float(match.group(1))
    unit = match.group(2) or ""
    if unit.startswith(("ч", "h")):
        minutes = int(value * 60)
    else:
        minutes = int(value)
    if not 30 <= minutes <= 24 * 60:
        raise ValidationError(
            user_message="Длительность — от 30 минут до 24 часов."
        )
    return minutes


def clean_text(raw: str, *, max_length: int = 4000, field: str = "Текст") -> str:
    text = raw.strip()
    if not text:
        raise ValidationError(user_message=f"{field} не может быть пустым.")
    if len(text) > max_length:
        raise ValidationError(
            user_message=f"{field} слишком длинный: {len(text)} символов "
            f"при лимите {max_length}."
        )
    return text


def parse_phone(raw: str) -> str:
    """Телефон ответственного.

    Принимаем в любом виде, храним в нормальном: +7XXXXXXXXXX.
    Люди пишут и «8 900», и «+7(900)», и с дефисами.
    """
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        raise ValidationError(user_message="Введите номер телефона.")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        raise ValidationError(
            user_message=(
                "Не похоже на номер. Нужно 11 цифр, например "
                "+7 900 123-45-67."
            )
        )
    return f"+{digits}"
