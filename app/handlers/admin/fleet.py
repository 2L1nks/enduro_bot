"""Управление парком: карточки техники, статусы, обслуживание."""
from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Experience, MotorcycleStatus, User
from app.exceptions import DomainError, ValidationError
from app.keyboards.admin import (
    back_to_admin,
    category_choice,
    fleet_list,
    moto_card_keyboard,
    status_choice,
)
from app.keyboards.callbacks import AdminCB, FleetCB, PageCB
from app.repositories import motorcycle as moto_repo
from app.services import fleet as fleet_service
from app.states.admin import FleetForm, MaintenanceForm
from app.utils.formatters import local, escape, moto_card
from app.utils import screen
from app.utils.logger import get_logger
from app.utils.timeslots import TimeWindow
from app.utils.validators import clean_text, parse_date_range

log = get_logger(__name__)
router = Router(name="admin_fleet")


@router.callback_query(AdminCB.filter(F.action == "fleet"))
async def show_fleet(
    call: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    motorcycles = await moto_repo.list_all(session, include_retired=True)

    if not motorcycles:
        await screen.show(call, "В парке пока нет техники.\n\n"
            "Добавьте первый мотоцикл — без этого записи оформлять не на что.", fleet_list([]))
        await call.answer()
        return

    by_status = await moto_repo.count_by_status(session)
    lines = [f"<b>Парк</b> — всего {len(motorcycles)}\n"]
    for status, count in sorted(by_status.items(), key=lambda x: x[0].value):
        from app.db.models import RU_MOTO_STATUS
        from app.utils.formatters import MOTO_ICON

        lines.append(f"{MOTO_ICON[status.value]} {RU_MOTO_STATUS[status]}: {count}")

    await screen.show(call, "\n".join(lines), fleet_list(motorcycles))
    await call.answer()


@router.callback_query(PageCB.filter(F.scope == "fleet"))
async def fleet_page(
    call: CallbackQuery, callback_data: PageCB, session: AsyncSession
) -> None:
    motorcycles = await moto_repo.list_all(session, include_retired=True)
    await call.message.edit_reply_markup(
        reply_markup=fleet_list(motorcycles, page=callback_data.page)
    )
    await call.answer()


@router.callback_query(PageCB.filter(F.scope == "noop"))
async def page_noop(call: CallbackQuery) -> None:
    """Счётчик страниц — не кнопка, но нажать по нему могут."""
    await call.answer()


async def _render_moto(
    call: CallbackQuery, session: AsyncSession, moto_id: int, note: str = ""
) -> bool:
    """Карточка техники со списком простоев. False — мотоцикл исчез."""
    moto = await moto_repo.get(session, moto_id)
    if moto is None:
        await call.answer("Мотоцикл не найден.", show_alert=True)
        return False

    periods = await moto_repo.maintenance_periods(
        session, moto.id, since=datetime.now(tz=settings.tz)
    )
    text = moto_card(moto)
    if periods:
        text += "\n\n<b>Запланированные простои</b>"
        for p in periods[:5]:
            text += (
                f"\n🔧 {local(p.period.lower):%d.%m} – {local(p.period.upper):%d.%m}"
                f" · {escape(p.reason) or 'без причины'}"
            )
    if note:
        text += f"\n\n{note}"

    can_delete = not await moto_repo.has_history(session, moto.id)
    await screen.show(call, text, moto_card_keyboard(moto, periods, can_delete=can_delete))
    return True


@router.callback_query(FleetCB.filter(F.action == "view"))
async def view_moto(
    call: CallbackQuery, callback_data: FleetCB, session: AsyncSession
) -> None:
    await _render_moto(call, session, callback_data.moto_id)
    await call.answer()


@router.callback_query(FleetCB.filter(F.action == "status"))
async def ask_status(call: CallbackQuery, callback_data: FleetCB) -> None:
    await screen.show(call, "Новый статус мотоцикла:", status_choice(callback_data.moto_id))
    await call.answer()


@router.callback_query(FleetCB.filter(F.action == "set_status"))
async def set_status(
    call: CallbackQuery, callback_data: FleetCB, user: User, session: AsyncSession
) -> None:
    try:
        status = MotorcycleStatus(callback_data.value)
    except ValueError:
        await call.answer("Неизвестный статус.", show_alert=True)
        return

    try:
        await fleet_service.set_status(
            session, callback_data.moto_id, status, actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    await _render_moto(call, session, callback_data.moto_id)
    await call.answer("Статус изменён")


@router.callback_query(FleetCB.filter(F.action == "retire"))
async def retire_moto(
    call: CallbackQuery, callback_data: FleetCB, user: User, session: AsyncSession
) -> None:
    try:
        await fleet_service.retire(
            session, callback_data.moto_id, actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    await _render_moto(
        call,
        session,
        callback_data.moto_id,
        "⚫ Выведен из парка. История выездов сохранена, "
        "вернуть можно кнопкой ниже.",
    )
    await call.answer("Выведен из парка")


@router.callback_query(FleetCB.filter(F.action == "restore"))
async def restore_moto(
    call: CallbackQuery, callback_data: FleetCB, user: User, session: AsyncSession
) -> None:
    try:
        await fleet_service.restore(
            session, callback_data.moto_id, actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    await _render_moto(
        call, session, callback_data.moto_id, "♻️ Мотоцикл снова в строю."
    )
    await call.answer("Возвращён в парк")


@router.callback_query(FleetCB.filter(F.action == "delete"))
async def delete_moto(
    call: CallbackQuery, callback_data: FleetCB, user: User, session: AsyncSession
) -> None:
    """Полное удаление — только для техники без истории выездов."""
    try:
        removed = await fleet_service.delete_forever(
            session, callback_data.moto_id, actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    if not removed:
        await call.answer(
            "У мотоцикла есть история выездов — удалять нельзя. "
            "Выведите его из парка.",
            show_alert=True,
        )
        return

    motorcycles = await moto_repo.list_all(session, include_retired=True)
    await screen.show(call, f"🗑 Мотоцикл удалён. В парке осталось: {len(motorcycles)}", fleet_list(motorcycles))
    await call.answer("Удалён")


@router.callback_query(FleetCB.filter(F.action == "maint_del"))
async def cancel_maintenance(
    call: CallbackQuery, callback_data: FleetCB, user: User, session: AsyncSession
) -> None:
    """Снятие планового простоя. В moto_id лежит id периода."""
    from app.db.models import MaintenancePeriod

    period = await session.get(MaintenancePeriod, callback_data.moto_id)
    if period is None:
        await call.answer("Период уже снят.", show_alert=True)
        return

    moto_id = period.motorcycle_id
    try:
        await fleet_service.cancel_maintenance(
            session, callback_data.moto_id, actor_id=user.id
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    await _render_moto(
        call, session, moto_id, "✅ Простой снят, техника снова в календаре."
    )
    await call.answer("Снято")


# ---------- Добавление мотоцикла ----------


@router.callback_query(FleetCB.filter(F.action == "add"))
async def add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FleetForm.title)
    log.info("Начато добавление мотоцикла, tg_id=%s", call.from_user.id)
    await screen.show(call, "<b>Новый мотоцикл</b>\n\n"
        "Шаг 1 из 5. Название, как вы называете его между собой — "
        "например «KTM 300 #1».", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(FleetForm.title)
async def add_title(message: Message, state: FSMContext) -> None:
    try:
        title = clean_text(message.text or "", max_length=128, field="Название")
    except ValidationError as exc:
        await message.answer(exc.user_message)
        return

    await state.update_data(title=title)
    await state.set_state(FleetForm.model)
    await message.answer(
        "Шаг 2 из 5. Модель — например «KTM 300 EXC».\n"
        "Можно пропустить: отправьте «-»."
    )


@router.message(FleetForm.model)
async def add_model(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await state.update_data(model="" if text == "-" else text[:128])
    await state.set_state(FleetForm.reg_number)
    await message.answer("Шаг 3 из 5. Госномер. Можно пропустить: «-».")


@router.message(FleetForm.reg_number)
async def add_reg(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await state.update_data(reg_number=None if text == "-" else text[:32])
    await state.set_state(FleetForm.category)
    await message.answer(
        "Шаг 4 из 5. Для какого уровня эта техника?",
        reply_markup=category_choice(),
    )


@router.callback_query(FleetCB.filter(F.action == "cat"), FleetForm.category)
async def add_category(
    call: CallbackQuery, callback_data: FleetCB, state: FSMContext
) -> None:
    try:
        category = Experience(callback_data.value)
    except ValueError:
        await call.answer("Неизвестная категория.", show_alert=True)
        return

    await state.update_data(category=category.value)
    await state.set_state(FleetForm.limits)
    await screen.show(call, "Шаг 5 из 5. Ограничения по райдеру — три числа через пробел:\n"
        "<code>мин_рост макс_рост макс_вес</code>\n\n"
        "Например: <code>165 190 100</code>\n"
        "Любое можно заменить на 0, если не ограничено.\n"
        "Пропустить всё: отправьте «-»."
    )
    await call.answer()


@router.message(FleetForm.limits)
async def add_limits(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    text = (message.text or "").strip()
    min_h = max_h = max_w = None

    if text != "-":
        parts = text.split()
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            await message.answer(
                "Нужно три числа через пробел, например «165 190 100».\n"
                "Или «-», чтобы пропустить."
            )
            return
        values = [int(p) or None for p in parts]
        min_h, max_h, max_w = values

    data = await state.get_data()
    if "title" not in data or "category" not in data:
        # Данные формы потерялись — например, бот перезапустили
        # с FSM в памяти посреди заполнения
        log.warning("Форма мотоцикла потеряла данные: %s", sorted(data))
        await state.clear()
        await message.answer(
            "Данные формы потерялись — возможно, бот перезапускался.\n"
            "Начните добавление заново.", back_to_admin())
        return

    log.info("Сохраняю мотоцикл %r", data.get("title"))
    try:
        moto = await fleet_service.create(
            session,
            title=data["title"],
            model=data.get("model", ""),
            reg_number=data.get("reg_number"),
            category=Experience(data["category"]),
            min_rider_height_cm=min_h,
            max_rider_height_cm=max_h,
            max_rider_weight_kg=max_w,
            actor_id=user.id,
        )
    except DomainError as exc:
        await message.answer(exc.user_message)
        return

    log.info("Мотоцикл id=%s добавлен админом %s", moto.id, user.id)
    await state.clear()
    await message.answer(
        "✅ Мотоцикл добавлен\n\n" + moto_card(moto),
        reply_markup=moto_card_keyboard(moto),
    )


# ---------- Обслуживание ----------


@router.callback_query(FleetCB.filter(F.action == "maint"))
async def maint_start(
    call: CallbackQuery, callback_data: FleetCB, state: FSMContext
) -> None:
    await state.update_data(moto_id=callback_data.moto_id)
    await state.set_state(MaintenanceForm.period)
    await screen.show(call, "<b>Вывод на обслуживание</b>\n\n"
        "На какие даты? Формат:\n"
        "<code>12.11.2026 - 15.11.2026</code>\n\n"
        "Одна дата — весь день целиком. "
        "Конечная дата включается в период.", back_to_admin("✖ Отмена"))
    await call.answer()


@router.message(MaintenanceForm.period)
async def maint_period(message: Message, state: FSMContext) -> None:
    try:
        start, end = parse_date_range(message.text or "")
    except ValidationError as exc:
        await message.answer(exc.user_message)
        return

    await state.update_data(start=start.isoformat(), end=end.isoformat())
    await state.set_state(MaintenanceForm.reason)
    await message.answer(
        f"Период: {start:%d.%m.%Y} – {(end).strftime('%d.%m.%Y')} (не включая).\n\n"
        "Причина — например «замена масла». Можно «-»."
    )


@router.message(MaintenanceForm.reason)
async def maint_reason(
    message: Message, user: User, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    reason = (message.text or "").strip()
    reason = "" if reason == "-" else reason[:255]

    window = TimeWindow(
        datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
    )

    try:
        period, conflicts = await fleet_service.schedule_maintenance(
            session, data["moto_id"], window, reason, actor_id=user.id
        )
    except DomainError as exc:
        await message.answer(exc.user_message)
        return

    if period is None:
        # Конфликты: показываем, кого заденет, и предлагаем решить
        from aiogram.types import InlineKeyboardButton
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        lines = [
            "⚠️ <b>Есть пересечения с записями</b>\n",
            f"Затронуто записей: {len(conflicts)}\n",
        ]
        for item in conflicts[:10]:
            lines.append(f"• {escape(item.describe())}")
        lines.append(
            "\nСначала перенесите или отмените эти записи — "
            "или выведите технику всё равно, тогда разбираться "
            "с ними придётся вручную."
        )

        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(
                text="⚠️ Всё равно вывести",
                callback_data=FleetCB(
                    action="maint_force", moto_id=data["moto_id"]
                ).pack(),
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="‹ Отмена",
                callback_data=FleetCB(action="view", moto_id=data["moto_id"]).pack(),
            )
        )
        await state.update_data(reason=reason)
        await message.answer("\n".join(lines), reply_markup=kb.as_markup())
        return

    await state.clear()
    await message.answer(
        f"✅ Мотоцикл выведен на обслуживание\n"
        f"{window.start:%d.%m.%Y} – {window.end:%d.%m.%Y}\n"
        f"Календарь пересчитан, на эти даты техника недоступна.",
        reply_markup=back_to_admin(),
    )


@router.callback_query(FleetCB.filter(F.action == "maint_force"))
async def maint_force(
    call: CallbackQuery,
    callback_data: FleetCB,
    user: User,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if "start" not in data:
        await call.answer("Данные устарели, начните заново.", show_alert=True)
        await state.clear()
        return

    window = TimeWindow(
        datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
    )
    try:
        period, conflicts = await fleet_service.schedule_maintenance(
            session,
            callback_data.moto_id,
            window,
            data.get("reason", ""),
            actor_id=user.id,
            force=True,
        )
    except DomainError as exc:
        await call.answer(exc.user_message, show_alert=True)
        return

    await state.clear()
    await screen.show(call, f"⚠️ Мотоцикл выведен на обслуживание несмотря на {len(conflicts)} "
        f"пересечений.\n\nРазберитесь с затронутыми записями: "
        f"замените технику участникам или перенесите выезды.", back_to_admin())
    await call.answer()
    log.warning(
        "Принудительный вывод мотоцикла id=%s, конфликтов %d",
        callback_data.moto_id,
        len(conflicts),
    )
