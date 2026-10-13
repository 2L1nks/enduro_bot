"""Яндекс.Диск как хранилище клиентских фото.

Каждому клиенту заводится своя папка со случайным именем вида
`enduro/клиент-<32 шестнадцатеричных символа>`. Папка публикуется,
и ссылка на неё выдаётся только этому клиенту в боте.

Почему случайное имя, а не номер по порядку: публичная ссылка
Яндекс.Диска доступна любому, кто её знает. Папка `.../client-7`
подбирается перебором за минуту, а 32 случайных символа — нет.
Имя клиента в путь не попадает: публичная ссылка не должна
выдавать, кому принадлежит папка.

Токен берётся на https://yandex.ru/dev/disk/poligon/ —
нужен доступ к Диску в режиме «запись».
"""
from __future__ import annotations

import secrets

import aiohttp

from app.config import settings
from app.exceptions import DomainError
from app.utils.logger import get_logger

log = get_logger(__name__)

API = "https://cloud-api.yandex.net/v1/disk"
TIMEOUT = aiohttp.ClientTimeout(total=60)


class YandexDisk:
    """Тонкая обёртка над REST API Диска."""

    def __init__(self, token: str, root: str = "enduro"):
        self.token = token
        self.root = root.strip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"OAuth {self.token}"}

    async def _request(
        self, session: aiohttp.ClientSession, method: str, url: str, **kwargs
    ):
        async with session.request(
            method, url, headers=self._headers, **kwargs
        ) as response:
            if response.status == 401:
                raise DomainError(
                    user_message=(
                        "Яндекс.Диск отклонил токен. Проверьте "
                        "YANDEX_DISK_TOKEN в настройках."
                    )
                )
            if response.status == 507:
                raise DomainError(user_message="На Яндекс.Диске закончилось место.")
            # 409 при создании папки означает «уже существует» — это не ошибка
            if response.status >= 400 and response.status != 409:
                text = await response.text()
                log.error("Яндекс.Диск %s %s: %s", method, url, text[:300])
                raise DomainError(
                    user_message="Яндекс.Диск недоступен, попробуйте позже."
                )
            if response.content_type == "application/json":
                return response.status, await response.json()
            return response.status, {}

    async def ensure_folder(self, path: str) -> None:
        """Создаёт папку вместе с родительскими."""
        parts = path.strip("/").split("/")
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            current = ""
            for part in parts:
                current = f"{current}/{part}" if current else part
                await self._request(
                    session, "PUT", f"{API}/resources", params={"path": current}
                )

    async def upload(self, path: str, data: bytes, *, overwrite: bool = True) -> None:
        """Заливает файл по пути на Диске."""
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            _, body = await self._request(
                session,
                "GET",
                f"{API}/resources/upload",
                params={"path": path, "overwrite": str(overwrite).lower()},
            )
            href = body.get("href")
            if not href:
                raise DomainError(user_message="Яндекс.Диск не дал ссылку на загрузку.")

            async with session.put(href, data=data) as response:
                if response.status >= 400:
                    log.error("Загрузка на Диск не удалась: %s", response.status)
                    raise DomainError(
                        user_message="Не удалось загрузить файл на Яндекс.Диск."
                    )

    async def publish(self, path: str) -> str | None:
        """Публикует папку и возвращает публичную ссылку."""
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            await self._request(
                session, "PUT", f"{API}/resources/publish", params={"path": path}
            )
            _, body = await self._request(
                session,
                "GET",
                f"{API}/resources",
                params={"path": path, "fields": "public_url"},
            )
            return body.get("public_url")

    async def delete(self, path: str) -> None:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            await self._request(
                session,
                "DELETE",
                f"{API}/resources",
                params={"path": path, "permanently": "true"},
            )

    async def health(self) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                await self._request(session, "GET", API)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Яндекс.Диск недоступен: %s", exc)
            return False


def new_folder_token() -> str:
    """Случайное имя папки клиента.

    32 шестнадцатеричных символа: подобрать перебором нереально,
    а значит публичная ссылка остаётся приватной по факту.
    """
    return secrets.token_hex(16)


_client: YandexDisk | None = None


def get_disk() -> YandexDisk:
    global _client
    if _client is None:
        if not settings.yandex_disk_token:
            raise DomainError(
                user_message=(
                    "Яндекс.Диск не настроен: задайте YANDEX_DISK_TOKEN."
                )
            )
        _client = YandexDisk(settings.yandex_disk_token, settings.yandex_disk_root)
    return _client
