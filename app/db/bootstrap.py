"""Первичное наполнение БД: владелец и заготовки инфо-раздела."""
from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.db.models import InfoContent, User, UserRole
from app.db.session import get_session
from app.utils.logger import get_logger

log = get_logger(__name__)

DEFAULT_INFO = [
    ("about", "О прокате", "Расскажите здесь о компании. Текст редактируется из админки.", 10),
    ("fleet", "Наши мотоциклы", "Описание моделей и для кого они подходят.", 20),
    ("prices", "Цены", "Стоимость аренды и что в неё входит.", 30),
    ("rules", "Правила", "Требования к участникам, залог, ответственность.", 40),
    ("gear", "Экипировка", "Что выдаём, что нужно привезти с собой.", 50),
    ("route", "Маршрут и место сбора", "Как добраться до базы.", 60),
    ("contacts", "Контакты", "Телефон, мессенджеры, соцсети.", 70),
]


async def bootstrap() -> None:
    """Идемпотентно: можно звать при каждом старте."""
    async with get_session() as session:
        owner = await session.scalar(
            select(User).where(User.tg_id == settings.owner_tg_id)
        )
        if owner is None:
            session.add(
                User(
                    tg_id=settings.owner_tg_id,
                    full_name="Владелец",
                    role=UserRole.OWNER,
                )
            )
            log.info("Создан владелец с tg_id=%s", settings.owner_tg_id)
        elif owner.role != UserRole.OWNER:
            owner.role = UserRole.OWNER
            log.info("Роль владельца восстановлена для tg_id=%s", settings.owner_tg_id)

        existing = set(
            (await session.scalars(select(InfoContent.slug))).all()
        )
        for slug, title, body, order in DEFAULT_INFO:
            if slug not in existing:
                session.add(
                    InfoContent(slug=slug, title=title, body=body, sort_order=order)
                )
        log.info("Инфо-разделы проверены")
