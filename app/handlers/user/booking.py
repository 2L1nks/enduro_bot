"""Запись на выезд.

Записывается один человек — он главный по группе. Телеграм-аккаунты
товарищей необязательны: хватает имени и физических параметров,
которые нужны, чтобы на месте выдать подходящую технику.

Порядок шагов: дата → время → сколько человек → длительность →
аккаунты товарищей (можно пропустить) → кто каждый участник и его
параметры → телефон ответственного → подтверждение.

Бот помнит людей. Параметры ответственного берутся из его профиля,
товарищ без телеграма опознаётся по ФИО (у него свой аккаунт),
и при повторной записи прошлые данные предлагаются кнопкой
«Всё так» вместо ввода заново. Клиенту подставляется только то,
что он сам вводил, — см. app/services/people.py.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import RU_EXPERIENCE, Experience, User
from app.exceptions import DomainError, ValidationError
from app.keyboards.calendar import build_calendar, calendar_legend
from app.keyboards.callbacks import (
    CalendarCB,
    CompanionCB,
    ConfirmCB,
    DurationCB,
    MenuCB,
    ExpCB,
    SeatsCB,
    SlotCB,
)
from app.handlers.user.start import greeting
from app.keyboards.common import (
    back_to_menu,
    confirm_keyboard,
    durations_keyboard,
    main_menu,
    seats_keyboard,
    slots_keyboard,
)
from app.services import availability as avail
from app.services import booking as booking_service
from app.services import notifier
from app.services import people
from app.services.booking import ParticipantData
from app.states.booking import BookingFlow
from app.utils import screen
from app.utils.chat import drop_tracked
from app.utils.formatters import escape, money, user_label
from app.utils.logger import get_logger
from app.utils.timeslots import TimeWindow, humanize_duration
from app.utils.validators import (
    parse_duration,
    parse_height,
    parse_phone,
    parse_shoe,
    parse_weight,
)

log = get_logger(__name__)
router = Router(name="booking")

#: По какой длительности красим календарь, пока клиент её не выбрал
PREVIEW_MINUTES = 60

#: Телеграм-ник: латиница, цифры, подчёркивание, 5–32 символа
USERNAME_RE = re.compile(r"[A-Za-z0-9_]{5,32}")


async def _say(
    message: Message, text: str, keyboard=None
) -> None:
    """Очередной шаг формы вместо предыдущего экрана.

    Иначе переписка с ботом превращается в простыню: каждый шаг
    оставлял бы своё сообщение, и раздел не убирался при выходе.
    Введённый человеком текст тоже убирается — см. screen.reply.
    """
    await screen.reply(message, text, keyboard)


async def _warn(message: Message, text: str) -> None:
    """Подсказка при неверном вводе — уберётся на следующем шаге."""
    await screen.warn(message, text)


def _window(data: dict) -> TimeWindow:
    start = datetime.fromisoformat(data["start"])
    minutes = data.get("duration", PREVIEW_MINUTES)
    return TimeWindow(start, start + timedelta(minutes=minutes))


def _riders(data: dict) -> list[dict]:
    return list(data.get("riders") or [])


async def _show_calendar(
    call: CallbackQuery, session: AsyncSession, state: FSMContext, year: int, month: int
) -> None:
    days = await avail.month_overview(session, year, month, PREVIEW_MINUTES)
    await state.update_data(year=year, month=month)
    await state.set_state(BookingFlow.day)
    await screen.show(
        call,
        f"<b>Запись на выезд</b>\n\nШаг 1. {calendar_legend()}",
        build_calendar(year, month, days),
    )


@router.callback_query(MenuCB.filter(F.action == "book"))
async def start_booking(
    call: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    await drop_tracked(bot, call.message.chat.id)
    await state.clear()
    today = date.today()
    await _show_calendar(call, session, state, today.year, today.month)
    await call.answer()


@router.callback_query(CalendarCB.filter(F.action == "skip"))
async def calendar_skip(call: CallbackQuery) -> None:
    """Неактивная ячейка: гасим «часики», ничего не делаем."""
    await call.answer()


@router.callback_query(CalendarCB.filter(F.action == "nav"))
async def calendar_nav(
    call: CallbackQuery,
    callback_data: CalendarCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    today = date.today()
    target = date(callback_data.year, callback_data.month, 1)
    if target < date(today.year, today.month, 1):
        await call.answer("Это уже прошло.")
        return
    if target > date(today.year + 1, today.month, 1):
        await call.answer("Так далеко записаться нельзя.")
        return

    await _show_calendar(call, session, state, callback_data.year, callback_data.month)
    await call.answer()


@router.callback_query(CalendarCB.filter(F.action == "back"))
async def calendar_back(call: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await screen.show(call, greeting(user), main_menu(user))
    await call.answer()


@router.callback_query(CalendarCB.filter(F.action == "day"))
async def choose_day(
    call: CallbackQuery,
    callback_data: CalendarCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    day = date(callback_data.year, callback_data.month, callback_data.day)
    slots = await avail.day_slots(
        session, day, PREVIEW_MINUTES, step_minutes=settings.slot_step_minutes
    )
    if not slots:
        await call.answer("На этот день выездов уже нет.", show_alert=True)
        return

    await state.update_data(day=day.isoformat())
    await state.set_state(BookingFlow.slot)
    await screen.show(
        call,
        f"<b>{day:%d.%m.%Y}</b>\n\nШаг 2. Во сколько начинаем?",
        slots_keyboard(day, slots),
    )
    await call.answer()


@router.callback_query(SlotCB.filter())
async def choose_slot(
    call: CallbackQuery,
    callback_data: SlotCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    start = datetime.strptime(
        f"{callback_data.date}{callback_data.time}", "%Y%m%d%H%M"
    ).replace(tzinfo=settings.tz)

    free = await avail.count_free(
        session, TimeWindow(start, start + timedelta(minutes=PREVIEW_MINUTES))
    )
    if free == 0:
        await call.answer("Это время только что заняли.", show_alert=True)
        return

    await state.update_data(start=start.isoformat(), free=free)
    await state.set_state(BookingFlow.seats)
    await screen.show(
        call,
        f"<b>{start:%d.%m.%Y %H:%M}</b>\nСвободно мотоциклов: {free}\n\n"
        f"Шаг 3. Сколько вас человек, включая вас?",
        seats_keyboard(free),
    )
    await call.answer()


@router.callback_query(SeatsCB.filter(), BookingFlow.seats)
async def choose_seats(
    call: CallbackQuery, callback_data: SeatsCB, state: FSMContext
) -> None:
    data = await state.get_data()
    if callback_data.count > data.get("free", 0):
        await call.answer(
            f"Свободно только {data.get('free', 0)} мотоциклов.", show_alert=True
        )
        return

    await state.update_data(seats=callback_data.count)
    await state.set_state(BookingFlow.duration)
    await screen.show(
        call,
        f"Человек в группе: {callback_data.count}\n\n"
        "Шаг 4. На сколько времени берёте технику?",
        durations_keyboard(),
    )
    await call.answer()


@router.callback_query(DurationCB.filter(F.minutes == 0), BookingFlow.duration)
async def duration_custom_ask(call: CallbackQuery, state: FSMContext) -> None:
    """Произвольное время — вводится с клавиатуры."""
    await state.set_state(BookingFlow.duration_custom)
    await screen.show(
        call,
        "Введите длительность: например <code>2.5ч</code>, "
        "<code>45мин</code> или <code>90</code> (минуты).",
        back_to_menu(),
    )
    await call.answer()


@router.message(BookingFlow.duration_custom)
async def duration_custom_set(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    try:
        minutes = parse_duration(message.text or "")
    except ValidationError as exc:
        await _warn(message, exc.user_message)
        return
    await _apply_duration(message, session, state, user, minutes)


@router.callback_query(DurationCB.filter(), BookingFlow.duration)
async def duration_set(
    call: CallbackQuery,
    callback_data: DurationCB,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await _apply_duration(call.message, session, state, user, callback_data.minutes)
    await call.answer()


async def _apply_duration(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    minutes: int,
) -> None:
    """Проверяет, хватает ли техники на выбранную длительность."""
    data = await state.get_data()
    seats = data.get("seats", 1)
    start = datetime.fromisoformat(data["start"])
    window = TimeWindow(start, start + timedelta(minutes=minutes))

    enough, free = await avail.check_capacity(session, window, seats)
    if not enough:
        await _say(
            message,
            f"На {window} свободно мотоциклов: {free}, "
            f"а нужно {seats}.\n\nВыберите другое время или меньшую длительность.",
            back_to_menu(),
        )
        await state.clear()
        return

    await state.update_data(duration=minutes, riders=[], current=None, pending=None)
    await state.set_state(BookingFlow.usernames)

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="Пропустить", callback_data=ConfirmCB(action="skip").pack()
        )
    )
    if seats == 1:
        # Один человек — спрашивать аккаунты не о чем
        await state.update_data(usernames=[])
        await _next_rider(message, state, session, user)
        return

    await _say(
        message,
        f"<b>{window}</b> · {humanize_duration(minutes)}\n\n"
        f"Шаг 5. Телеграм-аккаунты товарищей — по одному в строке:\n"
        f"<code>@ivan_petrov\n@petr_sidorov</code>\n\n"
        f"Нужно {seats - 1}, но это необязательно — можно пропустить "
        f"и назвать людей по фамилии и имени на следующем шаге.",
        kb.as_markup(),
    )


def _person_key(rider: dict) -> str:
    """Кто это — чтобы не записать одного человека в группу дважды."""
    if rider.get("user_id"):
        return f"u{rider['user_id']}"
    if rider.get("key"):
        return f"k{rider['key']}"
    if rider.get("username"):
        return f"@{rider['username'].lower()}"
    return f"n{rider.get('name')}"


@router.callback_query(ConfirmCB.filter(F.action == "skip"), BookingFlow.usernames)
async def usernames_skip(
    call: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
) -> None:
    await state.update_data(usernames=[])
    await call.answer()
    await _next_rider(call.message, state, session, user)


@router.message(BookingFlow.usernames)
async def usernames_set(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    seats = data.get("seats", 1)

    names = [
        line.strip().lstrip("@")
        for line in (message.text or "").splitlines()
        if line.strip() and line.strip() != "-"
    ]
    if len(names) > seats - 1:
        await _warn(
            message,
            f"Аккаунтов больше, чем людей: указано {len(names)}, "
            f"а товарищей {seats - 1}. Проверьте список.",
        )
        return
    bad = next((n for n in names if not USERNAME_RE.fullmatch(n)), None)
    if bad is not None:
        await _warn(
            message,
            f"«{escape(bad[:40])}» не похоже на телеграм-ник: латиница, цифры "
            "и подчёркивание, от 5 символов. Например <code>@ivan_petrov</code>.",
        )
        return
    if len({n.lower() for n in names}) != len(names):
        await _warn(message, "Один и тот же ник указан дважды.")
        return

    await state.update_data(usernames=names)
    await _next_rider(message, state, session, user)


async def _next_rider(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Следующий участник: сначала кто он, затем его параметры."""
    data = await state.get_data()
    riders = _riders(data)
    usernames = data.get("usernames") or []
    index = len(riders)

    if index == 0:
        current = {
            "name": user.full_name or "Ответственный",
            "user_id": user.id,
            "username": user.username,
            "leader": True,
        }
        recalled = await people.recall(session, viewer=user, person=user)
        await _offer(message, state, current, recalled)
        return

    if index - 1 < len(usernames):
        username = usernames[index - 1]
        current = {"name": f"@{username}", "username": username, "leader": False}
        recalled = await people.recall(session, viewer=user, tg_username=username)
        await _offer(message, state, current, recalled)
        return

    await _ask_who(message, state, session, user)


async def _ask_who(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Товарищ без телеграма: выбрать из прошлых или назвать по ФИО."""
    data = await state.get_data()
    riders = _riders(data)
    seats = data.get("seats", 1)
    used = {_person_key(r) for r in riders}

    offered = []
    for companion in await people.previous_companions(session, user):
        probe = {"user_id": companion.user_id, "username": companion.tg_username}
        if _person_key(probe) not in used:
            offered.append(companion.as_dict())

    await state.update_data(companions=offered)
    await state.set_state(BookingFlow.rider_who)

    kb = InlineKeyboardBuilder()
    for index, companion in enumerate(offered):
        kb.row(
            InlineKeyboardButton(
                text=f"👤 {companion['label']}"[:60],
                callback_data=CompanionCB(index=index).pack(),
            )
        )

    lead = (
        "Выберите из тех, кого вы уже привозили, или напишите"
        if offered
        else "Напишите"
    )
    await _say(
        message,
        f"Шаг 6. <b>Участник {len(riders) + 1} из {seats}</b>\n\n"
        f"{lead} фамилию и имя:\n<code>Иванов Иван</code>\n\n"
        "По ФИО бот запомнит человека: в следующий раз его рост, вес "
        "и опыт подставятся сами. Если у него есть телеграм — "
        "напишите ник: <code>@ivan_petrov</code>.",
        kb.as_markup() if offered else None,
    )


@router.callback_query(CompanionCB.filter(), BookingFlow.rider_who)
async def rider_pick_companion(
    call: CallbackQuery,
    callback_data: CompanionCB,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    offered = data.get("companions") or []
    if not 0 <= callback_data.index < len(offered):
        await call.answer("Список устарел — напишите фамилию и имя.", show_alert=True)
        return

    chosen = offered[callback_data.index]
    if chosen.get("user_id"):
        person = await session.get(User, chosen["user_id"])
        if person is None:
            await call.answer("Человек не найден — напишите ФИО.", show_alert=True)
            return
        current = {"name": person.full_name, "user_id": person.id, "leader": False}
        recalled = await people.recall(session, viewer=user, person=person)
    else:
        username = chosen["username"]
        current = {"name": f"@{username}", "username": username, "leader": False}
        recalled = await people.recall(session, viewer=user, tg_username=username)

    await call.answer()
    await _offer(call.message, state, current, recalled)


@router.message(BookingFlow.rider_who)
async def rider_named(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()

    if text.startswith("@"):
        username = text.lstrip("@").split()[0] if text.lstrip("@") else ""
        if not USERNAME_RE.fullmatch(username):
            await _warn(
                message,
                "Телеграм-ник — латиница, цифры и подчёркивание, от 5 символов. "
                "Например <code>@ivan_petrov</code>. Или напишите фамилию и имя.",
            )
            return
        current = {"name": f"@{username}", "username": username, "leader": False}
        recalled = await people.recall(session, viewer=user, tg_username=username)
    else:
        try:
            display, key = people.normalize_fio(text)
        except ValidationError as exc:
            await _warn(message, exc.user_message)
            return
        person = await people.find_offline(session, key)
        if person is not None:
            current = {"name": person.full_name, "user_id": person.id, "leader": False}
            # Подставим, только если этого человека привозил сам клиент
            recalled = await people.recall(session, viewer=user, person=person)
        else:
            current = {"name": display, "key": key, "leader": False}
            recalled = None

    if _person_key(current) in {_person_key(r) for r in _riders(data)}:
        await _warn(message, f"{escape(current['name'])} уже в группе.")
        return
    await _offer(message, state, current, recalled)


async def _offer(
    message: Message,
    state: FSMContext,
    current: dict,
    recalled: "people.RiderParams | None",
) -> None:
    """Прошлые данные — на подтверждение, иначе спрашиваем заново."""
    data = await state.get_data()
    riders = _riders(data)
    seats = data.get("seats", 1)
    await state.update_data(
        current=current,
        recalled=recalled.as_dict() if recalled else None,
        pending=None,
    )

    who = "Вы (ответственный за группу)" if current.get("leader") else current["name"]
    head = f"Шаг 6. <b>{escape(who)}</b> — {len(riders) + 1} из {seats}\n\n"

    if recalled is not None:
        await state.set_state(BookingFlow.rider_recall)
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(
                text="✅ Всё так", callback_data=ConfirmCB(action="same").pack()
            ),
            InlineKeyboardButton(
                text="✏️ Изменить", callback_data=ConfirmCB(action="edit").pack()
            ),
        )
        await _say(
            message,
            head + f"В прошлый раз: {recalled.summary()}.\n\nВсё так?",
            kb.as_markup(),
        )
        return

    await state.set_state(BookingFlow.rider_params)
    await _say(
        message,
        head + "Рост, вес и размер ноги через пробел:\n<code>178 75 43</code>",
    )


@router.callback_query(ConfirmCB.filter(F.action == "same"), BookingFlow.rider_recall)
async def rider_same(
    call: CallbackQuery, user: User, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    recalled = people.RiderParams.from_dict(data.get("recalled"))
    if recalled is None:
        await call.answer("Данные устарели, начните заново.", show_alert=True)
        return
    await call.answer()

    if recalled.experience is None:
        # Рост и вес помним, а опыт раньше не спрашивали
        await state.update_data(
            pending={"h": recalled.height_cm, "w": recalled.weight_kg, "s": recalled.shoe_size}
        )
        await _ask_experience(call.message, state)
        return
    await _add_rider(call.message, state, session, user, recalled)


@router.callback_query(ConfirmCB.filter(F.action == "edit"), BookingFlow.rider_recall)
async def rider_edit(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = data.get("current") or {}
    await state.set_state(BookingFlow.rider_params)
    await _say(
        call.message,
        f"<b>{escape(current.get('name') or 'Участник')}</b>\n\n"
        "Рост, вес и размер ноги через пробел:\n<code>178 75 43</code>",
    )
    await call.answer()


@router.message(BookingFlow.rider_params)
async def rider_params(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3:
        await _warn(
            message,
            "Нужно три числа через пробел: рост вес размер_ноги.\n"
            "Например: 178 75 43",
        )
        return

    try:
        height = parse_height(parts[0])
        weight = parse_weight(parts[1])
        shoe = parse_shoe(parts[2])
    except ValidationError as exc:
        await _warn(message, exc.user_message)
        return

    await state.update_data(pending={"h": height, "w": weight, "s": shoe})
    await _ask_experience(message, state)


async def _ask_experience(message: Message, state: FSMContext) -> None:
    await state.set_state(BookingFlow.rider_experience)
    kb = InlineKeyboardBuilder()
    for idx, exp in enumerate(Experience):
        kb.row(
            InlineKeyboardButton(
                text=RU_EXPERIENCE[exp].capitalize(),
                callback_data=ExpCB(level=idx).pack(),
            )
        )
    await _say(message, "Опыт езды:", kb.as_markup())


@router.callback_query(ExpCB.filter(), BookingFlow.rider_experience)
async def rider_experience(
    call: CallbackQuery,
    callback_data: ExpCB,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    levels = list(Experience)
    if not 0 <= callback_data.level < len(levels):
        await call.answer("Неизвестный уровень.", show_alert=True)
        return

    data = await state.get_data()
    pending = data.get("pending") or {}
    if not pending:
        await call.answer("Данные устарели, начните заново.", show_alert=True)
        return

    await call.answer()
    params = people.RiderParams(
        pending["h"], pending["w"], pending["s"], levels[callback_data.level]
    )
    await _add_rider(call.message, state, session, user, params)


async def _add_rider(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    params: "people.RiderParams",
) -> None:
    data = await state.get_data()
    current = data.get("current")
    if not current:
        await _say(message, "Данные устарели — начните запись заново.", back_to_menu())
        await state.clear()
        return

    riders = _riders(data)
    riders.append(
        {
            "name": current["name"],
            "username": current.get("username"),
            "user_id": current.get("user_id"),
            "key": current.get("key"),
            "h": params.height_cm,
            "w": params.weight_kg,
            "s": params.shoe_size,
            "exp": params.experience.value,
            "leader": bool(current.get("leader")),
        }
    )
    await state.update_data(riders=riders, current=None, recalled=None, pending=None)

    if len(riders) < data.get("seats", 1):
        await _next_rider(message, state, session, user)
        return
    await _ask_phone(message, state, user)


async def _ask_phone(message: Message, state: FSMContext, user: User) -> None:
    await state.set_state(BookingFlow.phone)
    if user.phone:
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(
                text=f"✅ {user.phone}", callback_data=ConfirmCB(action="phone").pack()
            )
        )
        await _say(
            message,
            "Шаг 7. Телефон для связи — оставить прежний или написать другой:",
            kb.as_markup(),
        )
        return
    await _say(
        message,
        "Шаг 7. Телефон ответственного за группу — на случай, "
        "если нужно будет связаться:\n<code>+7 900 123-45-67</code>",
    )


@router.callback_query(ConfirmCB.filter(F.action == "phone"), BookingFlow.phone)
async def phone_same(call: CallbackQuery, user: User, state: FSMContext) -> None:
    if not user.phone:
        await call.answer("Номер не сохранён — напишите его.", show_alert=True)
        return
    await call.answer()
    await _show_summary(call.message, state, user.phone)


@router.message(BookingFlow.phone)
async def set_phone(message: Message, state: FSMContext) -> None:
    try:
        phone = parse_phone(message.text or "")
    except ValidationError as exc:
        await _warn(message, exc.user_message)
        return
    await _show_summary(message, state, phone)


async def _show_summary(message: Message, state: FSMContext, phone: str) -> None:
    data = await state.get_data()
    await state.update_data(phone=phone)
    await state.set_state(BookingFlow.confirm)

    window = _window(data)
    riders = _riders(data)
    total = settings.price_per_person * len(riders)

    lines = [
        "<b>Проверьте заявку</b>\n",
        f"Когда: {window}",
        f"Длительность: {humanize_duration(data['duration'])}",
        f"Место сбора: {escape(settings.meeting_point)}",
        f"Телефон: {escape(phone)}",
        f"\n<b>Группа ({len(riders)})</b>",
    ]
    for r in riders:
        mark = " ⭐" if r["leader"] else ""
        lines.append(
            f"• {escape(r['name'])}{mark} — {r['h']} см, {r['w']} кг, "
            f"нога {r['s']}, {RU_EXPERIENCE[Experience(r['exp'])]}"
        )
    if settings.price_per_person:
        lines.append(
            f"\nСтоимость: {money(settings.price_per_person)} × {len(riders)} "
            f"= <b>{money(total)}</b>"
        )
    lines.append(
        "\nЭто предварительная заявка — администратор подтвердит её "
        "и свяжется с вами."
    )

    await _say(message, "\n".join(lines), confirm_keyboard())


@router.callback_query(ConfirmCB.filter(F.action == "no"), BookingFlow.confirm)
async def cancel_flow(call: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await screen.show(call, "Заявка отменена.", main_menu(user))
    await call.answer()


@router.callback_query(ConfirmCB.filter(F.action == "yes"), BookingFlow.confirm)
async def submit(
    call: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    window = _window(data)
    riders = _riders(data)

    try:
        # Аккаунты по ФИО и заявка — одной точкой сохранения: если
        # технику успели занять, новые аккаунты не останутся сиротами
        async with session.begin_nested():
            participants = []
            for r in riders:
                if not r.get("user_id") and r.get("key"):
                    person = await people.get_or_create_offline(
                        session, r["name"], r["key"], added_by_id=user.id
                    )
                    r["user_id"] = person.id
                participants.append(
                    ParticipantData(
                        full_name=r["name"],
                        user_id=r.get("user_id"),
                        tg_username=r.get("username"),
                        height_cm=r["h"],
                        weight_kg=r["w"],
                        shoe_size=r["s"],
                        experience=Experience(r["exp"]),
                        is_leader=r["leader"],
                    )
                )
            booking = await booking_service.create_request(
                session,
                user=user,
                window=window,
                participants=participants,
                contact_phone=data.get("phone"),
            )
    except DomainError as exc:
        await state.clear()
        await screen.show(call, exc.user_message, back_to_menu())
        await call.answer()
        return

    # Запоминаем параметры — в следующий раз они подставятся сами
    for r in riders:
        if not r.get("user_id"):
            continue  # товарищ по нику без аккаунта — помнит его запись
        person = user if r["user_id"] == user.id else await session.get(User, r["user_id"])
        if person is not None:
            people.remember(
                person,
                people.RiderParams(r["h"], r["w"], r["s"], Experience(r["exp"])),
                actor=user,
                trusted=False,
            )

    # Телефон — в профиль: в следующий раз предложим его кнопкой
    if data.get("phone"):
        user.phone = data["phone"]

    await state.clear()
    await screen.show(
        call,
        "✅ <b>Заявка отправлена</b>\n\n"
        f"Заявка №{booking.id}\n"
        f"{window}, человек: {len(participants)}\n"
        f"Место сбора: {escape(settings.meeting_point)}\n\n"
        "Администратор подтвердит запись и напишет вам. "
        "Статус можно посмотреть в разделе «Мои записи».",
        back_to_menu(),
    )
    await call.answer("Заявка принята")
    log.info("Пользователь %s подал заявку id=%s", user.id, booking.id)

    await notifier.notify_staff(
        bot,
        session,
        f"🕓 <b>Новая заявка №{booking.id}</b>\n"
        f"Ответственный: {user_label(user)}\n"
        f"Телефон: {escape(data.get('phone') or '—')}\n"
        f"Когда: {window}\n"
        f"Человек: {len(participants)}\n\n"
        f"Подтвердить — /admin → Записи",
        keep_until_admin=True,
    )
