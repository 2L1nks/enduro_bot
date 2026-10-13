"""Люди в группах: опознание, аккаунты без телеграма, память о параметрах.

Три задачи.

1. Человек без телеграма получает аккаунт, который опознаётся по ФИО.
   ФИО приводится к ключу (нижний регистр, ё→е, одиночные пробелы),
   уникальность ключа держит индекс в базе.

2. Параметры для подбора техники — рост, вес, обувь, опыт —
   запоминаются в профиле и при следующей записи подставляются.

3. Кому что можно показать. Рост и вес — персональные данные.
   Админ видит всё. Клиенту подставляется только то, что он сам
   вводил раньше, и профили людей, которых он сам привозил. Иначе
   любой, кто знает чужой @username или ФИО, увидел бы чужие данные.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RU_EXPERIENCE, Booking, BookingParticipant, Experience, User
from app.exceptions import ValidationError
from app.utils.formatters import escape
from app.utils.logger import get_logger

log = get_logger(__name__)

#: Сколько прошлых товарищей предлагать кнопками
COMPANIONS_LIMIT = 6

_NAME_PART = re.compile(r"^[A-Za-zА-Яа-яЁё]+(?:[-'][A-Za-zА-Яа-яЁё]+)*$")


# ---------- ФИО ----------


def normalize_fio(raw: str) -> tuple[str, str]:
    """ФИО для показа и ключ для поиска.

    Нужны хотя бы фамилия и имя: по одному имени «Саша» опознавать
    человека нельзя — тёзок слишком много, и чужие данные подставились
    бы не тому.
    """
    text = re.sub(r"\s+", " ", (raw or "").strip())
    parts = text.split(" ") if text else []

    if len(parts) < 2:
        raise ValidationError(
            user_message=(
                "Нужны фамилия и имя, например: <code>Иванов Иван</code>.\n"
                "Отчество — по желанию, оно помогает отличить тёзок."
            )
        )
    if len(parts) > 4 or len(text) > 100:
        raise ValidationError(user_message="Слишком длинно — нужны только фамилия, имя и отчество.")
    for part in parts:
        if len(part) < 2 or not _NAME_PART.match(part):
            raise ValidationError(
                user_message=(
                    f"«{escape(part[:30])}» не похоже на часть имени. Только буквы, "
                    "дефис допустим: <code>Петрова-Водкина Анна</code>."
                )
            )

    display = " ".join("-".join(p.capitalize() for p in part.split("-")) for part in parts)
    key = display.lower().replace("ё", "е")
    return display, key


async def find_offline(session: AsyncSession, key: str) -> User | None:
    """Аккаунт без телеграма по ключу ФИО."""
    return await session.scalar(
        select(User).where(User.tg_id.is_(None), User.name_key == key)
    )


async def get_or_create_offline(
    session: AsyncSession, display: str, key: str, *, added_by_id: int | None
) -> User:
    """Найти или завести аккаунт без телеграма.

    Создание идёт в точке сохранения: если параллельно этого же
    человека внёс кто-то другой, база отвергнет двойника по индексу,
    и мы просто возьмём уже созданный аккаунт.
    """
    found = await find_offline(session, key)
    if found is not None:
        return found

    person = User(tg_id=None, name_key=key, full_name=display, added_by_id=added_by_id)
    try:
        async with session.begin_nested():
            session.add(person)
            await session.flush()
    except IntegrityError:
        found = await find_offline(session, key)
        if found is None:
            raise
        return found

    log.info("Заведён аккаунт без телеграма id=%s: %s", person.id, display)
    return person


# ---------- Параметры ----------


@dataclass(slots=True)
class RiderParams:
    height_cm: int
    weight_kg: int
    shoe_size: int
    experience: Experience | None = None

    def summary(self) -> str:
        line = f"рост {self.height_cm}, вес {self.weight_kg}, обувь {self.shoe_size}"
        if self.experience is not None:
            line += f", опыт — {RU_EXPERIENCE[self.experience]}"
        return line

    def as_dict(self) -> dict:
        return {
            "h": self.height_cm,
            "w": self.weight_kg,
            "s": self.shoe_size,
            "exp": self.experience.value if self.experience else None,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "RiderParams | None":
        if not data:
            return None
        exp = data.get("exp")
        return cls(data["h"], data["w"], data["s"], Experience(exp) if exp else None)


def profile_params(person: User | None) -> RiderParams | None:
    """Параметры из профиля, если они там полностью есть."""
    if person is None or not (person.height_cm and person.weight_kg and person.shoe_size):
        return None
    return RiderParams(
        person.height_cm, person.weight_kg, person.shoe_size, person.experience
    )


async def _last_participation(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    tg_username: str | None = None,
    created_by_id: int | None = None,
) -> RiderParams | None:
    """Параметры из последнего выезда этого человека.

    created_by_id сужает поиск записями одного клиента — так клиент
    получает обратно только то, что сам вводил.
    """
    if user_id is None and not tg_username:
        return None

    who = []
    if user_id is not None:
        who.append(BookingParticipant.user_id == user_id)
    if tg_username:
        who.append(func.lower(BookingParticipant.tg_username) == tg_username.lower())

    query = (
        select(BookingParticipant)
        .join(Booking, Booking.id == BookingParticipant.booking_id)
        .where(
            or_(*who),
            BookingParticipant.height_cm.is_not(None),
            BookingParticipant.weight_kg.is_not(None),
            BookingParticipant.shoe_size.is_not(None),
        )
        .order_by(Booking.starts_at.desc())
        .limit(1)
    )
    if created_by_id is not None:
        query = query.where(Booking.created_by_id == created_by_id)

    row = await session.scalar(query)
    if row is None:
        return None
    return RiderParams(row.height_cm, row.weight_kg, row.shoe_size, row.experience)


async def _brought_by(session: AsyncSession, person: User, client_id: int) -> bool:
    """Этого человека привозил сам клиент — значит, знает его данные."""
    if person.added_by_id == client_id:
        return True
    return bool(
        await session.scalar(
            select(
                exists().where(
                    BookingParticipant.user_id == person.id,
                    BookingParticipant.booking_id == Booking.id,
                    Booking.created_by_id == client_id,
                )
            )
        )
    )


async def recall(
    session: AsyncSession,
    *,
    viewer: User | None,
    person: User | None = None,
    tg_username: str | None = None,
) -> RiderParams | None:
    """Что подставить для человека — с учётом того, кто спрашивает.

    viewer=None — спрашивает админ: ему доступно всё.
    """
    if viewer is None:
        if person is not None:
            return profile_params(person) or await _last_participation(
                session, user_id=person.id
            )
        return await _last_participation(session, tg_username=tg_username)

    if person is not None and person.id == viewer.id:
        return profile_params(person) or await _last_participation(
            session, user_id=person.id
        )

    if person is not None and not person.has_telegram:
        if not await _brought_by(session, person, viewer.id):
            return None
        return profile_params(person) or await _last_participation(
            session, user_id=person.id, created_by_id=viewer.id
        )

    if tg_username:
        # Профиль чужого телеграм-аккаунта клиенту не показываем —
        # только то, что он сам вводил в своих прошлых записях
        return await _last_participation(
            session, tg_username=tg_username, created_by_id=viewer.id
        )
    return None


def remember(person: User, params: RiderParams, *, actor: User, trusted: bool) -> bool:
    """Сохранить параметры в профиль для следующей записи.

    Свой профиль человек правит сам; профиль без телеграма ничей —
    его обновляет тот, кто привёз; админ правит любой. Чужой
    телеграм-профиль клиент не перезаписывает: это данные другого
    человека, и он может их сам поддерживать.
    """
    allowed = trusted or person.id == actor.id or not person.has_telegram
    if not allowed:
        return False
    person.height_cm = params.height_cm
    person.weight_kg = params.weight_kg
    person.shoe_size = params.shoe_size
    if params.experience is not None:
        person.experience = params.experience
    return True


# ---------- Прошлые товарищи ----------


@dataclass(slots=True)
class Companion:
    """Человек, которого клиент уже привозил."""

    label: str
    user_id: int | None = None       # аккаунт без телеграма
    tg_username: str | None = None   # или телеграм-ник

    @property
    def key(self) -> str:
        return f"u{self.user_id}" if self.user_id else f"@{(self.tg_username or '').lower()}"

    def as_dict(self) -> dict:
        return {"label": self.label, "user_id": self.user_id, "username": self.tg_username}


async def previous_companions(
    session: AsyncSession, client: User, *, limit: int = COMPANIONS_LIMIT
) -> list[Companion]:
    """Кого клиент привозил раньше — свежие первыми.

    Только записи, которые клиент делал сам, и только люди без
    телеграма или указанные по нику. Телеграм-аккаунты, которых
    в группу добавлял админ, сюда не попадают: их данные клиент
    не вводил.
    """
    rows = await session.execute(
        select(BookingParticipant, User)
        .join(Booking, Booking.id == BookingParticipant.booking_id)
        .outerjoin(User, User.id == BookingParticipant.user_id)
        .where(
            Booking.created_by_id == client.id,
            BookingParticipant.is_leader.is_(False),
            or_(
                BookingParticipant.user_id.is_(None),
                BookingParticipant.user_id != client.id,
            ),
            or_(
                User.tg_id.is_(None) & User.id.is_not(None),
                BookingParticipant.tg_username.is_not(None),
            ),
        )
        .order_by(Booking.starts_at.desc())
        .limit(limit * 5)
    )

    seen: set[str] = set()
    result: list[Companion] = []
    for participant, person in rows:
        if person is not None and not person.has_telegram:
            item = Companion(label=person.full_name, user_id=person.id)
        elif participant.tg_username:
            item = Companion(
                label=f"@{participant.tg_username}", tg_username=participant.tg_username
            )
        else:
            continue
        if item.key in seen:
            continue
        seen.add(item.key)
        result.append(item)
        if len(result) >= limit:
            break
    return result
