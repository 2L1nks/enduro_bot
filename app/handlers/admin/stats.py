"""Статистика и экспорт."""
from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.keyboards.admin import back_to_admin
from app.keyboards.calendar import MONTHS
from app.keyboards.callbacks import AdminCB, StatsCB
from app.repositories import admin as admin_repo
from app.repositories import availability as avail_repo
from app.services import export as export_service
from app.utils import screen
from app.utils.chat import drop_message
from app.utils.formatters import escape, money, plural, user_label
from app.utils.logger import get_logger

log = get_logger(__name__)
router = Router(name="admin_stats")


RIDES = ("выезд", "выезда", "выездов")
TRIPS = ("поездка", "поездки", "поездок")
PEOPLE = ("человек", "человека", "человек")


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Начало месяца и начало следующего — в часовом поясе проката."""
    since = datetime(year, month, 1, tzinfo=settings.tz)
    until = (
        datetime(year + 1, 1, 1, tzinfo=settings.tz)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=settings.tz)
    )
    return since, until


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


async def _render(
    session: AsyncSession, year: int, month: int
) -> tuple[str, InlineKeyboardMarkup]:
    now = datetime.now(tz=settings.tz)
    since, until = _month_bounds(year, month)
    is_current = since <= now < until
    is_future = since > now

    report = await admin_repo.month_report(session, since, until, now)
    load = await avail_repo.motorcycle_load(session, since, until)
    clients = await admin_repo.top_clients(session, limit=5)

    title = f"{MONTHS[month - 1].capitalize()} {year}"
    lines = [f"<b>📊 Статистика · {title}</b>"]

    empty = not (
        report.rides_done or report.rides_ahead or report.cancelled
        or report.no_shows or report.received
    )
    if empty:
        # Столбик нулей читается как поломка — говорим прямо
        lines.append("\nЗа этот месяц записей и оплат нет.")

    if not is_future and not empty:
        lines += [
            "\n<b>Проведено</b>",
            f"{plural(report.rides_done, RIDES)}, {plural(report.riders_done, PEOPLE)}",
        ]
        if report.no_shows:
            lines.append(f"Не пришли: {report.no_shows}")
    if report.rides_ahead:
        lines += [
            "\n<b>Впереди</b>" if is_current or is_future else "\n<b>Не закрыто</b>",
            f"{plural(report.rides_ahead, RIDES)}, {plural(report.riders_ahead, PEOPLE)}",
        ]
        if report.pending:
            lines.append(f"🕓 Ждут подтверждения: {report.pending}")
    if report.cancelled:
        lines.append(f"Отменено: {report.cancelled}")

    if not empty:
        lines += [
            "\n<b>Деньги</b>",
            f"Получено: {money(report.received)}",
        ]
    if report.debt:
        lines.append(f"⚠️ Не доплачено за проведённые: {money(report.debt)}")
    if report.expected:
        lines.append(f"Ожидается с предстоящих: {money(report.expected)}")

    busy = [(m, t, h) for m, t, h in load if t]
    if load and not empty:
        lines.append("\n<b>Загрузка техники</b>")
        for moto, trips, hours in busy[:10]:
            lines.append(
                f"• {escape(moto.title)} — {plural(trips, RIDES)}, {hours:.0f} ч"
            )
        idle = len(load) - len(busy)
        if idle:
            lines.append(f"• без выездов: {idle}")

    if clients:
        lines.append("\n<b>Постоянные клиенты</b> · за всё время")
        for person, trips, spent in clients:
            lines.append(
                f"• {user_label(person)} — {plural(trips, TRIPS)}, {money(spent)}"
            )

    prev_y, prev_m = _shift(year, month, -1)
    next_y, next_m = _shift(year, month, 1)
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f"‹ {MONTHS[prev_m - 1].capitalize()}",
            callback_data=StatsCB(year=prev_y, month=prev_m).pack(),
        ),
        InlineKeyboardButton(
            text=f"{MONTHS[next_m - 1].capitalize()} ›",
            callback_data=StatsCB(year=next_y, month=next_m).pack(),
        ),
    )
    if not is_current:
        kb.row(
            InlineKeyboardButton(
                text="Текущий месяц",
                callback_data=StatsCB(year=now.year, month=now.month).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="📤 Выгрузить в Excel",
            callback_data=AdminCB(action="export").pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="‹ В админку", callback_data=AdminCB(action="home").pack()
        )
    )
    return "\n".join(lines), kb.as_markup()


@router.callback_query(AdminCB.filter(F.action == "stats"))
async def show_stats(
    call: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    now = datetime.now(tz=settings.tz)
    text, keyboard = await _render(session, now.year, now.month)
    await screen.show(call, text, keyboard)
    await call.answer()


@router.callback_query(StatsCB.filter())
async def stats_month(
    call: CallbackQuery, callback_data: StatsCB, session: AsyncSession
) -> None:
    if not 1 <= callback_data.month <= 12:
        await call.answer()
        return
    text, keyboard = await _render(session, callback_data.year, callback_data.month)
    await screen.show(call, text, keyboard)
    await call.answer()


@router.callback_query(AdminCB.filter(F.action == "export"))
async def export_data(
    call: CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot
) -> None:
    await call.answer("Готовлю файл…")
    chat_id = call.message.chat.id

    try:
        data, filename = await export_service.export_xlsx(session)
    except Exception:
        log.exception("Ошибка экспорта")
        await screen.show(
            call,
            "Не удалось сформировать файл. Подробности в логах сервера.",
            back_to_admin(),
        )
        return

    # Экран, с которого нажали «Экспорт», убираем: иначе после
    # возврата в админку на экране окажется два меню сразу
    await drop_message(bot, chat_id, call.message.message_id)

    sent = await bot.send_document(
        chat_id,
        BufferedInputFile(data, filename=filename),
        caption=(
            "Выгрузка записей: одна строка — один участник выезда.\n"
            "Открывается в Excel и Google Таблицах."
        ),
        reply_markup=back_to_admin(),
    )
    # Файл становится текущим экраном — при выходе он уберётся
    screen.remember(chat_id, sent.message_id)
