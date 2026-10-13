"""Конфигурация приложения. Всё берётся из .env, ничего не хардкодится."""
from __future__ import annotations

from datetime import time
from functools import lru_cache
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    bot_token: str
    owner_tg_id: int

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "enduro"
    postgres_user: str = "enduro"
    postgres_password: str = "enduro"

    # FSM-хранилище: memory для разработки (Windows без Redis),
    # redis для прода — состояние переживает перезапуск бота.
    fsm_storage: Literal["memory", "redis"] = "memory"
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # Хранилище фото:
    #   yadisk — папка клиента на Яндекс.Диске (боевой режим),
    #   local  — папка на диске рядом с проектом (разработка),
    #   s3     — MinIO или Yandex Object Storage.
    storage_backend: Literal["local", "s3", "yadisk"] = "local"
    yandex_disk_token: str = ""
    yandex_disk_root: str = "enduro"
    local_storage_path: Path = Path("storage")
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "enduro-photos"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # Бизнес-правила
    booking_buffer_minutes: int = 30
    max_group_size: int = 8
    # Единое место сбора и единая цена: в записи их больше не спрашиваем
    meeting_point: str = "Уточните у администратора"
    price_per_person: Decimal = Decimal("0")

    # Варианты длительности аренды в минутах, через запятую.
    # Храним строкой: списочные поля pydantic-settings пытается
    # разобрать как JSON ещё до валидаторов, и "120,240,480"
    # роняет запуск. Готовый список отдаёт свойство rental_durations.
    rental_durations_raw: str = Field(
        default="60,90,120,180", validation_alias="RENTAL_DURATIONS"
    )
    # Шаг сетки слотов в календаре, минуты
    slot_step_minutes: int = 60
    workday_start: time = time(9, 0)
    workday_end: time = time(20, 0)
    timezone: str = "Europe/Moscow"

    log_level: str = "INFO"

    @field_validator("workday_start", "workday_end", mode="before")
    @classmethod
    def _parse_time(cls, v):
        if isinstance(v, str):
            hh, mm = v.split(":")
            return time(int(hh), int(mm))
        return v

    @property
    def rental_durations(self) -> list[int]:
        """Длительности аренды списком, отсортированные по возрастанию."""
        values = [
            int(x.strip())
            for x in self.rental_durations_raw.split(",")
            if x.strip().isdigit()
        ]
        return sorted(values) or [120, 240, 480]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def database_url(self) -> str:
        """URL для asyncpg.

        Логин и пароль экранируем: символы @ # / ? в пароле иначе
        разрывают URL, а в паролях их ставят постоянно.
        """
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
