"""Перехват всего, что не нашло своего обработчика.

Молчащий бот — худший вид поломки: человек не понимает, сломалось
что-то или он сам что-то сделал не так. Этот роутер включается
последним и превращает тишину в понятное сообщение, а в логах
оставляет точные данные кнопки и состояние формы — по ним
видно, какого обработчика не хватило.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards import callbacks as cb
from app.keyboards.common import back_to_menu
from app.utils.logger import get_logger

#: Все классы кнопок — для разбора непойманных данных
ALL_CALLBACKS = [
    getattr(cb, name)
    for name in dir(cb)
    if name.endswith("CB") and isinstance(getattr(cb, name), type)
]

log = get_logger(__name__)
router = Router(name="fallback")


def _diagnose(data: str | None) -> str:
    """Почему ни один фильтр не принял эти данные.

    Пробуем разобрать строку всеми известными классами кнопок
    и показываем результат. Без этого «кнопка не работает»
    приходится искать вслепую.
    """
    if not data:
        return "данных нет"

    prefix = data.split(":", 1)[0]
    lines = []
    for cls in ALL_CALLBACKS:
        if getattr(cls, "__prefix__", None) != prefix:
            continue
        try:
            parsed = cls.unpack(data)
        except Exception as exc:  # noqa: BLE001 — нас интересует любая причина
            lines.append(f"{cls.__name__}: {type(exc).__name__}: {exc}")
        else:
            lines.append(f"{cls.__name__}: разобрано успешно → {parsed!r}")

    if not lines:
        return f"ни один класс кнопок не объявляет префикс {prefix!r}"
    return " | ".join(lines)


@router.callback_query()
async def unmatched_callback(call: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    log.warning(
        "НЕ ОБРАБОТАНО: кнопка data=%r состояние=%s от tg_id=%s\n"
        "   разбор: %s",
        call.data,
        current,
        call.from_user.id if call.from_user else "?",
        _diagnose(call.data),
    )
    await call.answer(
        "Эта кнопка больше не активна — сообщение устарело.\n"
        "Откройте меню заново: /menu",
        show_alert=True,
    )


@router.message()
async def unmatched_message(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    log.warning(
        "НЕ ОБРАБОТАНО: сообщение состояние=%s тип=%s от tg_id=%s",
        current,
        message.content_type,
        message.from_user.id if message.from_user else "?",
    )

    if current:
        # Форма открыта, но шаг не принял ввод — скорее всего,
        # ждали другой тип сообщения
        await message.answer(
            "Не удалось обработать это сообщение на текущем шаге.\n"
            "Начните заново: /menu"
        )
    else:
        await message.answer(
            "Я понимаю только команды и кнопки меню.",
            reply_markup=back_to_menu(),
        )
