"""Хранилище файлов: локальная папка или S3.

Один интерфейс, две реализации. Локальная — для разработки на Windows
без MinIO; S3 — для боевого сервера. Переключается переменной
STORAGE_BACKEND, код приложения разницы не видит.

Ссылки наружу не отдаются принципиально: фото уходит клиенту через
бота после проверки владельца, поэтому presigned-URL здесь нет.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.utils.logger import get_logger

log = get_logger(__name__)


def build_key(owner_id: int, extension: str = "jpg") -> str:
    """Путь вида 2026/11/u42/8f3a....jpg.

    Раскладка по годам и месяцам нужна, чтобы в одной папке
    не скопились десятки тысяч файлов — иначе листинг
    начинает тормозить и у локальной ФС, и у S3.
    """
    now = datetime.now(tz=settings.tz)
    return f"{now:%Y/%m}/u{owner_id}/{uuid.uuid4().hex}.{extension.lstrip('.')}"


class Storage(ABC):
    """Общий интерфейс хранилища."""

    @abstractmethod
    async def save(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    async def load(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def health(self) -> bool:
        """Доступно ли хранилище. Проверяется при старте бота."""


class LocalStorage(Storage):
    """Папка на диске. Для разработки."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Защита от выхода за пределы папки через ../ в ключе
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError(f"Недопустимый ключ: {key}")
        return target

    async def save(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def load(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    async def health(self) -> bool:
        try:
            probe = self.root / ".health"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError as exc:
            log.error("Локальное хранилище недоступно: %s", exc)
            return False


class S3Storage(Storage):
    """S3-совместимое хранилище: MinIO или Yandex Object Storage."""

    def __init__(self) -> None:
        self.bucket = settings.s3_bucket
        self._session = None

    def _client(self):
        from aiobotocore.session import get_session

        if self._session is None:
            self._session = get_session()
        return self._session.create_client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    async def save(self, key: str, data: bytes, content_type: str) -> None:
        async with self._client() as client:
            await client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )

    async def load(self, key: str) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self.bucket, Key=key)
            async with response["Body"] as stream:
                return await stream.read()

    async def delete(self, key: str) -> None:
        async with self._client() as client:
            await client.delete_object(Bucket=self.bucket, Key=key)

    async def health(self) -> bool:
        try:
            async with self._client() as client:
                await client.head_bucket(Bucket=self.bucket)
            return True
        except Exception as exc:  # noqa: BLE001 — любая ошибка значит «недоступно»
            log.error("S3 недоступен (%s): %s", settings.s3_endpoint, exc)
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    """Хранилище по настройке STORAGE_BACKEND."""
    global _storage
    if _storage is not None:
        return _storage

    if settings.storage_backend == "s3":
        log.info("Хранилище: S3 (%s)", settings.s3_endpoint)
        _storage = S3Storage()
    else:
        path = Path(settings.local_storage_path)
        log.info("Хранилище: локальная папка %s", path.resolve())
        _storage = LocalStorage(path)

    return _storage
