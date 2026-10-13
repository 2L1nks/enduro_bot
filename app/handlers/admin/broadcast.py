"""Ручные рассылки."""
from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Broadcast, User
from app.exceptions import ValidationError
from app.keyboards.admin import back_to_admin, broadcast_targets
from app.keyboards.callbacks import AdminCB, BroadcastCB
from app.repositories import admin as admin_repo
from app.scheduler import flush_now
from app.services import notifier
from app.states.admin import BroadcastForm
from app.utils.formatters import user_label
from app.utils import screen
from app.utils.logger import get_logger
from app.utils.validators import clean_text

log = get_logger(__name__)
router = Router(name="admin_broadcast")

TARGET_NAMES = {
    "all": "всем клиентам",
    "upcoming": "участникам ближайших выездов",
    "one": "одному клиенту",
}


@router.callback_query(AdminCB.filter(F.action == "broadcast"))
async def broadcast_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BroadcastForm.target)
    await screen.show(call, "<b>Рассылка</b>\n\nКому отправляем?", broadcast_targets())
    await call.answer()


@router.callback_query(BroadcastCB.filter(F.action == "target"), BroadcastForm.target)
async def broadcast_target(
    call: CallbackQuery,
    callback_data: BroadcastCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    target = callback_data.value

    # «Одному клиенту» — сначала надо его найти
    if target == "one":
        await state.set_state(BroadcastForm.pick_user)
        await screen.show(call, "Кому пишем? Введите имя, @username или Telegram ID.", back_to_admin("✖ Отмена"))
        await call.answer()
        return

    recipients = await admin_repo.broadcast_audience(session, target)

    if not recipients:
        await screen.show(call, "Получателей нет — отправлять некому.", back_to_admin())
        await state.clear()
        await call.answer()
        return

    await state.update_data(target=target, count=len(recipients))
    await state.set_state(BroadcastForm.text)
    await screen.show(call, f"Получателей: <b>{len(recipients)}</b> "
        f"({TARGET_NAMES.get(target, target)})\n\n"
        "Отправьте текст сообщения. Поддерживается разметка Telegram: "
        "<b>жирный</b>, <i>курсив</i>.", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(BroadcastForm.pick_user)
async def broadcast_find_user(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите хотя бы два символа.")
        return

    found = await admin_repo.search_users(session, query, limit=10, telegram_only=True)
    if not found:
        await message.answer(f"Никого не нашёл по запросу «{query}».")
        return

    kb = InlineKeyboardBuilder()
    for person in found:
        kb.row(
            InlineKeyboardButton(
                text=user_label(person)[:60],
                callback_data=BroadcastCB(
                    action="one", value=str(person.id)
                ).pack(),
            )
        )
    await message.answer("Кому отправляем?", reply_markup=kb.as_markup())


@router.callback_query(BroadcastCB.filter(F.action == "one"), BroadcastForm.pick_user)
async def broadcast_one_picked(
    call: CallbackQuery,
    callback_data: BroadcastCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    person = await session.get(User, int(callback_data.value or 0))
    if person is None:
        await call.answer("Клиент не найден.", show_alert=True)
        return

    await state.update_data(target="one", one_id=person.id, count=1)
    await state.set_state(BroadcastForm.text)
    await screen.show(call, f"Получатель: <b>{user_label(person)}</b>\n\nОтправьте текст сообщения.", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(BroadcastForm.text)
async def broadcast_preview(message: Message, state: FSMContext) -> None:
    try:
        text = clean_text(message.text or "", max_length=3500, field="Текст рассылки")
    except ValidationError as exc:
        await message.answer(exc.user_message)
        return

    data = await state.get_data()
    await state.update_data(text=text)
    await state.set_state(BroadcastForm.confirm)

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f"📢 Отправить ({data['count']})",
            callback_data=BroadcastCB(action="send").pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="✖ Отмена", callback_data=AdminCB(action="home").pack()
        )
    )

    await message.answer(
        "<b>Предпросмотр</b>\n"
        "─────────────\n"
        f"{text}\n"
        "─────────────\n"
        f"Получателей: {data['count']} ({TARGET_NAMES.get(data['target'])})",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(BroadcastCB.filter(F.action == "send"), BroadcastForm.confirm)
async def broadcast_send(
    call: CallbackQuery,
    user: User,
    session: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    data = await state.get_data()
    text = data.get("text", "")
    target = data.get("target", "all")

    if target == "one":
        person = await session.get(User, data.get("one_id", 0))
        recipients = [person] if person else []
    else:
        recipients = await admin_repo.broadcast_audience(session, target)

    if not recipients:
        await call.answer("Получателей больше нет.", show_alert=True)
        await state.clear()
        return

    broadcast = Broadcast(
        author_id=user.id,
        text=text,
        audience={"type": target},
        total=len(recipients),
    )
    session.add(broadcast)
    await session.flush()

    # В очередь, а не напрямую: сотня сообщений подряд упрётся
    # в лимиты Telegram и подвесит этот хендлер
    queued = await notifier.queue_broadcast(
        session, recipients, text, broadcast_id=broadcast.id
    )

    await state.clear()
    await screen.show(call, f"📢 Рассылка запущена: {queued} получателей.\n\n"
        "Сообщения уходят постепенно, чтобы не упереться в лимиты "
        "Telegram. Когда всё разойдётся, пришлю отчёт.", back_to_admin())
    await call.answer("Отправляется")
    log.info("Админ %s запустил рассылку на %d человек", user.id, queued)

    # Коммитим здесь: фоновая задача работает в своей сессии и должна
    # увидеть строки очереди. Без этого она найдёт пустую очередь.
    await session.commit()

    # Отправку ведёт воркер в фоне — хендлер не должен ждать,
    # пока уйдут сотни сообщений
    asyncio.create_task(
        flush_now(bot, report_to=user.tg_id), name=f"broadcast-{broadcast.id}"
    )
