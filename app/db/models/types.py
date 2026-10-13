"""Хелперы для типов колонок."""
from __future__ import annotations

from typing import Type

from sqlalchemy import Enum as SAEnum


def pg_enum(enum_cls: Type, name: str) -> SAEnum:
    """PostgreSQL ENUM, хранящий .value (строку), а не .name.

    Без values_callable SQLAlchemy пишет в БД имена членов (BEGINNER),
    а нам нужны нижнерегистровые значения из enums.py.
    """
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda e: [member.value for member in e],
        native_enum=True,
        create_constraint=False,
        validate_strings=True,
    )
