"""Пошаговое внесение группы администратором.

Логика шагов:
  дата и время -> длительность -> проверка свободной техники ->
  участники по кругу (кто это, затем параметры) ->
  телефон ответственного -> подтверждение -> запись с резервом.

Участник — либо человек из базы (с телеграмом или без), либо новый
человек без телеграма: для него заводится аккаунт по ФИО. Параметры
каждого запоминаются в профиле, и в следующий раз предлагаются
кнопкой «Взять эти данные».

Состояние копится в FSM; запись и новые аккаунты создаются одной
транзакцией в конце, чтобы брошенная на полпути форма не оставляла
мусор в базе. Вся форма — один экран: введённый текст убирается.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import RU_EXPERIENCE, Experience, User
from app.exceptions import DomainError, ValidationError
from app.keyboards.admin import back_to_admin, booking_card_keyboard
from app.keyboards.callbacks import AdmBookingCB, AdminCB
from app.repositories import admin as admin_repo
from app.services import availability as avail
from app.services import booking as booking_service
from app.services import notifier
from app.services import people
from app.services.booking import ParticipantData
from app.states.admin import GroupForm
from app.utils import screen
from app.utils.formatters import booking_card, escape, local, money, user_label
from app.utils.logger import get_logger
from app.utils.timeslots import TimeWindow, humanize_duration
from app.utils.validators import (
    parse_datetime,
    parse_height,
    parse_phone,
    parse_shoe,
    parse_time,
    parse_weight,
)

log = get_logger(__name__)
router = Router(name="admin_group")

CANCEL = back_to_admin("✖ Отменить внесение")

WEEKDAYS_FULL = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def _day_from_value(value: int) -> date | None:
    """Дата из кнопки календаря: 20261014 -> 14.10.2026."""
    if not value:
        return None
    try:
        return date(value // 10000, value // 100 % 100, value % 100)
    except ValueError:
        return None


def _person_key(item: dict) -> str:
    if item.get("user_id"):
        return f"u{item['user_id']}"
    return f"k{item.get('key')}"


def _participant_line(p: dict) -> str:
    exp = p.get("experience")
    exp_text = f", {RU_EXPERIENCE[Experience(exp)]}" if exp else ""
    return (
        f"• {escape(p['full_name'])} — {p['height_cm']} см, "
        f"{p['weight_kg']} кг, нога {p['shoe_size']}{exp_text}"
    )


# ---------- Дата, время, длительность ----------


@router.callback_query(AdmBookingCB.filter(F.action == "add_group"))
async def group_start(
    call: CallbackQuery, callback_data: AdmBookingCB, state: FSMContext
) -> None:
    await state.clear()
    await state.set_state(GroupForm.datetime)

    day = _day_from_value(callback_data.value)
    if day is not None:
        # Пришли из календаря — дата уже известна, нужно только время
        await state.update_data(day=day.isoformat())
        await screen.show(
            call,
            f"<b>Внесение группы</b> · {day:%d.%m.%Y}, {WEEKDAYS_FULL[day.weekday()]}\n\n"
            "Шаг 1. Время выезда:\n<code>10:00</code>",
            CANCEL,
        )
    else:
        await screen.show(
            call,
            "<b>Внесение группы</b>\n\n"
            "Шаг 1. Дата и время выезда:\n"
            "<code>15.11.2026 10:00</code>",
            CANCEL,
        )
    await call.answer()


@router.message(GroupForm.datetime)
async def group_datetime(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        if data.get("day"):
            day = date.fromisoformat(data["day"])
            at = parse_time(message.text or "")
            start = datetime.combine(day, at, tzinfo=settings.tz)
        else:
            start = parse_datetime(message.text or "")
    except ValidationError as exc:
        await screen.warn(message, exc.user_message)
        return

    if start < datetime.now(tz=settings.tz) - timedelta(hours=1):
        await screen.warn(message, "Это время уже прошло. Введите будущее время.")
        return

    await state.update_data(start=start.isoformat())
    await state.set_state(GroupForm.duration)

    kb = InlineKeyboardBuilder()
    for minutes in settings.rental_durations:
        kb.row(
            InlineKeyboardButton(
                text=humanize_duration(minutes),
                callback_data=AdmBookingCB(action="grp_dur", value=minutes).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="✖ Отменить внесение", callback_data=AdminCB(action="home").pack()
        )
    )
    await screen.reply(
        message,
        f"Выезд {start:%d.%m.%Y %H:%M}.\n\nШаг 2. Длительность:",
        kb.as_markup(),
    )


@router.callback_query(AdmBookingCB.filter(F.action == "grp_dur"), GroupForm.duration)
async def group_duration(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    start = datetime.fromisoformat(data["start"])
    window = TimeWindow(start, start + timedelta(minutes=callback_data.value))

    free = await avail.count_free(session, window)
    if free == 0:
        await state.clear()
        await screen.show(
            call,
            f"На {window} свободной техники нет.\n\n"
            "Выберите другое время или освободите мотоциклы.",
            back_to_admin(),
        )
        await call.answer()
        return

    await state.update_data(duration=callback_data.value, free=free, participants=[])
    await state.set_state(GroupForm.participant_search)
    await screen.show(
        call,
        f"<b>{window}</b>\nСвободно мотоциклов: {free}\n\n"
        "Шаг 3. Участники. Первый — ответственный за группу.\n\n"
        "Введите имя, @username или Telegram ID, чтобы найти человека "
        "в базе, или фамилию и имя нового человека без телеграма.",
        CANCEL,
    )
    await call.answer()


# ---------- Кто участник ----------


@router.message(GroupForm.participant_search)
async def group_search(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await screen.warn(message, "Введите хотя бы два символа для поиска.")
        return

    data = await state.get_data()
    already = {_person_key(p) for p in data.get("participants", [])}

    found = await admin_repo.search_users(session, query)
    kb = InlineKeyboardBuilder()
    shown = 0
    for person in found:
        if f"u{person.id}" in already:
            continue
        kb.row(
            InlineKeyboardButton(
                text=f"👤 {user_label(person)}"[:60],
                callback_data=AdmBookingCB(action="grp_user", value=person.id).pack(),
            )
        )
        shown += 1

    # Новый человек без телеграма — аккаунт по ФИО. Кнопка есть,
    # только если введённое похоже на фамилию и имя.
    try:
        display, key = people.normalize_fio(query)
    except ValidationError:
        display = key = None
    if display:
        await state.update_data(typed_fio=display, typed_key=key)
        kb.row(
            InlineKeyboardButton(
                text=f"➕ Новый без телеграма: {display}"[:60],
                callback_data=AdmBookingCB(action="grp_new").pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="✖ Отменить внесение", callback_data=AdminCB(action="home").pack()
        )
    )

    if shown:
        header = f"По запросу «{escape(query)}» нашлось — кого добавляем?"
    elif display:
        header = (
            f"В базе нет «{escape(query)}».\n\n"
            "Можно завести человека без телеграма — бот запомнит его по ФИО."
        )
    else:
        header = (
            f"В базе нет «{escape(query)}».\n\n"
            "Чтобы завести человека без телеграма, нужны фамилия и имя: "
            "<code>Иванов Иван</code>."
        )
    await screen.reply(message, header, kb.as_markup())


@router.callback_query(AdmBookingCB.filter(F.action == "grp_new"), GroupForm.participant_search)
async def group_new_person(
    call: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    """Человек без телеграма. Если такое ФИО уже есть — это он."""
    data = await state.get_data()
    display, key = data.get("typed_fio"), data.get("typed_key")
    if not key:
        await call.answer("ФИО не распознано, введите заново.", show_alert=True)
        return

    already = {_person_key(p) for p in data.get("participants", [])}
    existing = await people.find_offline(session, key)
    if existing is not None:
        if f"u{existing.id}" in already:
            await call.answer(f"{existing.full_name} уже в группе.", show_alert=True)
            return
        current = {"full_name": existing.full_name, "user_id": existing.id}
        recalled = await people.recall(session, viewer=None, person=existing)
        note = "Такой человек уже есть в базе — берём его.\n\n"
    else:
        if f"k{key}" in already:
            await call.answer(f"{display} уже в группе.", show_alert=True)
            return
        current = {"full_name": display, "key": key}
        recalled = None
        note = "Новый человек без телеграма — аккаунт заведётся при сохранении.\n\n"

    await call.answer()
    await _offer(call.message, state, current, recalled, note=note)


@router.callback_query(AdmBookingCB.filter(F.action == "grp_user"), GroupForm.participant_search)
async def group_pick_user(
    call: CallbackQuery,
    callback_data: AdmBookingCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    person = await session.get(User, callback_data.value)
    if person is None:
        await call.answer("Человек не найден.", show_alert=True)
        return

    current = {
        "full_name": person.full_name or user_label(person),
        "user_id": person.id,
        "username": person.username,
    }
    recalled = await people.recall(session, viewer=None, person=person)
    await call.answer()
    await _offer(call.message, state, current, recalled)


async def _offer(
    message: Message,
    state: FSMContext,
    current: dict,
    recalled: "people.RiderParams | None",
    *,
    note: str = "",
) -> None:
    """Прошлые данные — кнопкой, иначе ввод с нуля."""
    data = await state.get_data()
    number = len(data.get("participants", [])) + 1
    role = " · ответственный" if number == 1 else ""
    head = f"<b>{escape(current['full_name'])}</b> — участник {number}{role}\n\n{note}"

    await state.update_data(
        current=current,
        recalled=recalled.as_dict() if recalled else None,
        pending_participant=None,
    )

    kb = InlineKeyboardBuilder()
    if recalled is not None:
        await state.set_state(GroupForm.participant_recall)
        kb.row(
            InlineKeyboardButton(
                text="✅ Взять эти данные",
                callback_data=AdmBookingCB(action="grp_same").pack(),
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="✏️ Ввести заново",
                callback_data=AdmBookingCB(action="grp_edit").pack(),
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="✖ Отменить внесение", callback_data=AdminCB(action="home").pack()
            )
        )
        await screen.replace(
            message.bot,
            message.chat.id,
            head + f"В прошлый раз: {recalled.summary()}.",
            kb.as_markup(),
        )
        return

    await state.set_state(GroupForm.participant_params)
    await screen.replace(
        message.bot,
        message.chat.id,
        head + "Параметры через пробел: <code>рост вес размер_ноги</code>\n"
        "Например: <code>178 75 43</code>",
        CANCEL,
    )


@router.callback_query(AdmBookingCB.filter(F.action == "grp_same"), GroupForm.participant_recall)
async def group_same(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    recalled = people.RiderParams.from_dict(data.get("recalled"))
    if recalled is None:
        await call.answer("Данные устарели, найдите человека заново.", show_alert=True)
        return
    await state.update_data(
        pending_participant={
            "height_cm": recalled.height_cm,
            "weight_kg": recalled.weight_kg,
            "shoe_size": recalled.shoe_size,
        }
    )
    await call.answer()
    if recalled.experience is None:
        await _ask_experience(call.message, state)
        return
    await _add_participant(call.message, state, recalled.experience)


@router.callback_query(AdmBookingCB.filter(F.action == "grp_edit"), GroupForm.participant_recall)
async def group_edit(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = data.get("current") or {}
    await state.set_state(GroupForm.participant_params)
    await screen.show(
        call,
        f"<b>{escape(current.get('full_name') or 'Участник')}</b>\n\n"
        "Параметры через пробел: <code>рост вес размер_ноги</code>\n"
        "Например: <code>178 75 43</code>",
        CANCEL,
    )
    await call.answer()


@router.message(GroupForm.participant_params)
async def group_params(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3:
        await screen.warn(
            message,
            "Нужно три числа через пробел: рост вес размер_ноги.\nНапример: 178 75 43",
        )
        return
    try:
        height = parse_height(parts[0])
        weight = parse_weight(parts[1])
        shoe = parse_shoe(parts[2])
    except ValidationError as exc:
        await screen.warn(message, exc.user_message)
        return

    await state.update_data(
        pending_participant={"height_cm": height, "weight_kg": weight, "shoe_size": shoe}
    )
    await _ask_experience(message, state)


async def _ask_experience(message: Message, state: FSMContext) -> None:
    await state.set_state(GroupForm.participant_params)
    kb = InlineKeyboardBuilder()
    for idx, exp in enumerate(Experience):
        kb.row(
            InlineKeyboardButton(
                text=RU_EXPERIENCE[exp].capitalize(),
                callback_data=AdmBookingCB(action="grp_exp", value=idx).pack(),
            )
        )
    await screen.reply(message, "Уровень подготовки:", kb.as_markup())


@router.callback_query(AdmBookingCB.filter(F.action == "grp_exp"), GroupForm.participant_params)
async def group_experience(
    call: CallbackQuery, callback_data: AdmBookingCB, state: FSMContext
) -> None:
    levels = list(Experience)
    if not 0 <= callback_data.value < len(levels):
        await call.answer("Неизвестный уровень.", show_alert=True)
        return
    await call.answer()
    await _add_participant(call.message, state, levels[callback_data.value])


async def _add_participant(message: Message, state: FSMContext, experience: Experience) -> None:
    data = await state.get_data()
    current = data.get("current")
    pending = data.get("pending_participant")
    if not current or not pending:
        await state.clear()
        await screen.replace(
            message.bot, message.chat.id, "Данные устарели, начните заново.", back_to_admin()
        )
        return

    participants = list(data.get("participants", []))
    participants.append({**current, **pending, "experience": experience.value})
    await state.update_data(
        participants=participants, current=None, recalled=None, pending_participant=None
    )
    await state.set_state(GroupForm.participant_more)

    free = data.get("free", 0)
    lines = [f"<b>В группе: {len(participants)}</b>\n"]
    lines += [_participant_line(p) for p in participants]

    kb = InlineKeyboardBuilder()
    if len(participants) < min(free, settings.max_group_size):
        kb.row(
            InlineKeyboardButton(
                text="➕ Ещё участник", callback_data=AdmBookingCB(action="grp_more").pack()
            )
        )
    else:
        lines.append(
            f"\nДостигнут предел: свободно {free}, максимум в группе {settings.max_group_size}."
        )
    kb.row(
        InlineKeyboardButton(
            text="✓ Группа собрана", callback_data=AdmBookingCB(action="grp_done").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(text="✖ Отменить", callback_data=AdminCB(action="home").pack())
    )
    await screen.replace(message.bot, message.chat.id, "\n".join(lines), kb.as_markup())


@router.callback_query(AdmBookingCB.filter(F.action == "grp_more"), GroupForm.participant_more)
async def group_more(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GroupForm.participant_search)
    await screen.show(
        call,
        "Следующий участник: имя, @username или Telegram ID — "
        "или фамилия и имя нового человека без телеграма.",
        CANCEL,
    )
    await call.answer()


# ---------- Телефон, проверка, сохранение ----------


@router.callback_query(AdmBookingCB.filter(F.action == "grp_done"), GroupForm.participant_more)
async def group_phone_ask(
    call: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    leader = (data.get("participants") or [{}])[0]
    known = None
    if leader.get("user_id"):
        person = await session.get(User, leader["user_id"])
        known = person.phone if person else None

    await state.update_data(known_phone=known)
    await state.set_state(GroupForm.meeting_point)

    kb = InlineKeyboardBuilder()
    if known:
        kb.row(
            InlineKeyboardButton(
                text=f"✅ {known}", callback_data=AdmBookingCB(action="grp_phone").pack()
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="Без телефона", callback_data=AdmBookingCB(action="grp_nophone").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="✖ Отменить внесение", callback_data=AdminCB(action="home").pack()
        )
    )
    await screen.show(
        call,
        f"Шаг 4. Телефон ответственного ({escape(leader.get('full_name') or '—')}).\n"
        + ("Оставить прежний или написать другой." if known else "Напишите номер.")
        + f"\n\nМесто сбора и цена — из настроек: {escape(settings.meeting_point)}, "
        f"{money(settings.price_per_person)} с человека.",
        kb.as_markup(),
    )
    await call.answer()


@router.callback_query(AdmBookingCB.filter(F.action == "grp_phone"), GroupForm.meeting_point)
async def group_phone_same(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await call.answer()
    await _show_check(call.message, state, data.get("known_phone"))


@router.callback_query(AdmBookingCB.filter(F.action == "grp_nophone"), GroupForm.meeting_point)
async def group_phone_none(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _show_check(call.message, state, None)


@router.message(GroupForm.meeting_point)
async def group_phone_typed(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    phone = None
    if text != "-":
        try:
            phone = parse_phone(text)
        except ValidationError as exc:
            await screen.warn(message, exc.user_message)
            return
    await _show_check(message, state, phone)


async def _show_check(message: Message, state: FSMContext, phone: str | None) -> None:
    await state.update_data(phone=phone)
    data = await state.get_data()
    participants = data.get("participants", [])
    start = datetime.fromisoformat(data["start"])
    window = TimeWindow(start, start + timedelta(minutes=data["duration"]))
    price = settings.price_per_person

    await state.set_state(GroupForm.confirm)
    lines = [
        "<b>Проверьте данные</b>\n",
        f"Когда: {window}",
        f"Длительность: {humanize_duration(data['duration'])}",
        f"Место сбора: {escape(settings.meeting_point)}",
        f"Телефон: {escape(phone) or '—'}",
        f"\n<b>Участники ({len(participants)})</b>",
    ]
    lines += [_participant_line(p) for p in participants]
    new_people = [p for p in participants if p.get("key") and not p.get("user_id")]
    if new_people:
        lines.append(
            f"\nНовых аккаунтов без телеграма: {len(new_people)} — "
            "заведутся при сохранении."
        )
    lines.append(
        f"\nЦена: {money(price)} × {len(participants)} = "
        f"<b>{money(price * len(participants))}</b>"
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✅ Создать запись", callback_data=AdmBookingCB(action="grp_save").pack()
        )
    )
    kb.row(
        InlineKeyboardButton(text="✖ Отменить", callback_data=AdminCB(action="home").pack())
    )
    await screen.reply(message, "\n".join(lines), kb.as_markup())


@router.callback_query(AdmBookingCB.filter(F.action == "grp_save"), GroupForm.confirm)
async def group_save(
    call: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    raw = data.get("participants", [])
    if not raw:
        await call.answer("Группа пуста.", show_alert=True)
        return

    start = datetime.fromisoformat(data["start"])
    window = TimeWindow(start, start + timedelta(minutes=data["duration"]))

    try:
        # Новые аккаунты, запись и резерв — одной точкой сохранения:
        # если техника не нашлась, ничего из этого не останется
        async with session.begin_nested():
            participants = []
            for idx, p in enumerate(raw):
                if not p.get("user_id") and p.get("key"):
                    person = await people.get_or_create_offline(
                        session, p["full_name"], p["key"], added_by_id=user.id
                    )
                    p["user_id"] = person.id
                participants.append(
                    ParticipantData(
                        full_name=p["full_name"],
                        user_id=p.get("user_id"),
                        tg_username=p.get("username"),
                        height_cm=p["height_cm"],
                        weight_kg=p["weight_kg"],
                        shoe_size=p["shoe_size"],
                        experience=Experience(p["experience"]),
                        is_leader=(idx == 0),
                    )
                )
            # Место сбора и цена едины для всех — берутся из настроек
            booking = await booking_service.create_request(
                session,
                user=user,
                window=window,
                participants=participants,
                contact_phone=data.get("phone"),
            )
            await booking_service.confirm(session, booking.id, actor_id=user.id)
    except DomainError as exc:
        await state.clear()
        await screen.show(call, f"Не удалось создать запись.\n\n{exc.user_message}", back_to_admin())
        await call.answer()
        return

    # Запоминаем параметры — в следующий раз админ возьмёт их кнопкой
    for idx, p in enumerate(raw):
        person = await session.get(User, p["user_id"]) if p.get("user_id") else None
        if person is None:
            continue
        people.remember(
            person,
            people.RiderParams(
                p["height_cm"], p["weight_kg"], p["shoe_size"], Experience(p["experience"])
            ),
            actor=user,
            trusted=True,
        )
        if idx == 0 and data.get("phone"):
            person.phone = data["phone"]

    fresh = await admin_repo.get_booking(session, booking.id)
    await state.clear()
    await notifier.schedule_booking_reminders(session, fresh)

    # Сообщаем участникам — для них это новость
    meeting = fresh.meeting_point or settings.meeting_point
    for participant in fresh.participants:
        if participant.user is None or not participant.user.has_telegram:
            continue
        try:
            await notifier.push(
                bot,
                participant.user.tg_id,
                f"✅ Вы записаны на выезд {local(fresh.starts_at):%d.%m %H:%M}."
                f"\nМесто сбора: {escape(meeting)}"
                "\n\nНапомним за сутки и за два часа до выезда.",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось уведомить tg_id=%s: %s", participant.user.tg_id, exc)

    await screen.show(
        call,
        "✅ <b>Запись создана</b>\n\n" + booking_card(fresh, for_admin=True),
        booking_card_keyboard(fresh),
    )
    await call.answer("Готово")
    log.info("Админ %s внёс группу: запись id=%s", user.id, booking.id)
