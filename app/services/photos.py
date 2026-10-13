"""Работа с фотографиями клиентов.

Правило доступа: фото уходит человеку только через бота и только
после проверки владельца в запросе. Ключ в хранилище наружу не
показывается никогда, ссылки не выдаются — перебирать нечего.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Photo, User
from app.exceptions import DomainError
from app.services.storage import build_key, get_storage
from app.utils.logger import get_logger

log = get_logger(__name__)

MAX_PHOTO_BYTES = 20 * 1024 * 1024


async def ensure_client_folder(session: AsyncSession, owner: User) -> str:
    """Личная папка клиента на Яндекс.Диске.

    Создаётся при первой загрузке и сразу публикуется: ссылка нужна,
    чтобы клиент мог открыть все свои снимки одним нажатием.
    Имя папки случайное — публичная ссылка неперебираема, а по пути
    нельзя понять, чьи это фото.
    """
    from app.services.yadisk import get_disk, new_folder_token

    if owner.disk_folder and owner.disk_public_url:
        return owner.disk_folder

    disk = get_disk()
    folder = owner.disk_folder or f"{disk.root}/client-{new_folder_token()}"

    await disk.ensure_folder(folder)
    public_url = await disk.publish(folder)

    owner.disk_folder = folder
    owner.disk_public_url = public_url
    await session.flush()

    log.info("Клиенту %s заведена папка на Диске", owner.id)
    return folder


async def upload_to_disk(
    session: AsyncSession,
    bot: Bot,
    *,
    file_id: str,
    owner: User,
    uploaded_by_id: int,
    booking_id: int | None = None,
    caption: str | None = None,
) -> Photo:
    """Кладёт снимок в папку клиента на Яндекс.Диске."""
    from app.services.yadisk import get_disk

    tg_file = await bot.get_file(file_id)
    if tg_file.file_size and tg_file.file_size > MAX_PHOTO_BYTES:
        raise DomainError(user_message="Файл слишком большой, лимит 20 МБ.")

    buffer = await bot.download_file(tg_file.file_path)
    data = buffer.read()

    extension = (tg_file.file_path or "photo.jpg").rsplit(".", 1)[-1].lower()
    if extension not in ("jpg", "jpeg", "png", "webp"):
        extension = "jpg"

    folder = await ensure_client_folder(session, owner)
    name = f"{datetime.now(tz=settings.tz):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.{extension}"
    path = f"{folder}/{name}"

    await get_disk().upload(path, data)

    photo = Photo(
        s3_key=path,
        disk_path=path,
        telegram_file_id=file_id,
        owner_id=owner.id,
        booking_id=booking_id,
        uploaded_by_id=uploaded_by_id,
        mime_type=f"image/{'jpeg' if extension == 'jpg' else extension}",
        size_bytes=len(data),
        caption=caption,
    )
    session.add(photo)
    await session.flush()

    log.info("Фото id=%s загружено на Диск для клиента %s", photo.id, owner.id)
    return photo


async def upload_from_telegram(
    session: AsyncSession,
    bot: Bot,
    *,
    file_id: str,
    owner_id: int,
    uploaded_by_id: int,
    booking_id: int | None = None,
    caption: str | None = None,
) -> Photo:
    """Сохраняет присланное админом фото в хранилище.

    telegram_file_id кешируем: повторная выдача клиенту тогда идёт
    без скачивания из хранилища — Telegram отдаёт файл сам.
    """
    tg_file = await bot.get_file(file_id)
    if tg_file.file_size and tg_file.file_size > MAX_PHOTO_BYTES:
        raise DomainError(user_message="Файл слишком большой, лимит 20 МБ.")

    buffer = await bot.download_file(tg_file.file_path)
    data = buffer.read()

    extension = (tg_file.file_path or "photo.jpg").rsplit(".", 1)[-1].lower()
    if extension not in ("jpg", "jpeg", "png", "webp"):
        extension = "jpg"

    key = build_key(owner_id, extension)
    await get_storage().save(key, data, f"image/{'jpeg' if extension == 'jpg' else extension}")

    photo = Photo(
        s3_key=key,
        telegram_file_id=file_id,
        owner_id=owner_id,
        booking_id=booking_id,
        uploaded_by_id=uploaded_by_id,
        mime_type=f"image/{'jpeg' if extension == 'jpg' else extension}",
        size_bytes=len(data),
        caption=caption,
    )
    session.add(photo)
    await session.flush()

    log.info("Фото id=%s сохранено для пользователя %s", photo.id, owner_id)
    return photo


async def user_photos(
    session: AsyncSession, owner_id: int, *, limit: int = 50
) -> list[Photo]:
    """Фото конкретного человека. Фильтр по владельцу — в самом запросе."""
    rows = await session.scalars(
        select(Photo)
        .where(Photo.owner_id == owner_id)
        .order_by(Photo.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


async def count_photos(session: AsyncSession, owner_id: int) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(Photo).where(Photo.owner_id == owner_id)
        )
        or 0
    )


async def send_photo(
    bot: Bot, session: AsyncSession, chat_id: int, photo: Photo
) -> int | None:
    """Отправляет фото в чат и возвращает id отправленного сообщения.

    Id нужен вызывающему коду, чтобы потом убрать снимок из чата.

    Сначала пробуем по кешированному telegram_file_id — это мгновенно
    и не нагружает хранилище. Если Telegram файл забыл, достаём из
    хранилища и обновляем кеш.
    """
    caption = photo.caption or None

    if photo.telegram_file_id:
        try:
            sent = await bot.send_photo(
                chat_id, photo.telegram_file_id, caption=caption
            )
            return sent.message_id
        except Exception as exc:  # noqa: BLE001
            log.info("file_id устарел для фото id=%s: %s", photo.id, exc)

    from aiogram.types import BufferedInputFile

    data = await get_storage().load(photo.s3_key)
    sent = await bot.send_photo(
        chat_id,
        BufferedInputFile(data, filename=f"photo_{photo.id}.jpg"),
        caption=caption,
    )
    if sent.photo:
        photo.telegram_file_id = sent.photo[-1].file_id
        await session.flush()
    return sent.message_id


async def delete_photo(session: AsyncSession, photo: Photo) -> None:
    try:
        if photo.disk_path:
            from app.services.yadisk import get_disk

            await get_disk().delete(photo.disk_path)
        else:
            await get_storage().delete(photo.s3_key)
    except Exception as exc:  # noqa: BLE001 — запись удаляем в любом случае
        log.warning("Не удалось стереть файл %s: %s", photo.s3_key, exc)
    await session.delete(photo)
    await session.flush()
