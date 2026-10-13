"""Старт и главное меню."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.db.models import User
from app.keyboards.callbacks import MenuCB
from app.keyboards.common import main_menu
from app.utils import screen
from app.utils.chat import drop_message

router = Router(name="start")

#: Один и тот же текст при любом заходе в меню.
#: Короткое «Главное меню:» схлопывало ширину сообщения, и кнопки
#: прыгали в размерах — выглядело как поломка.
GREETING = (
    "Привет, {name}!\n\n"
    "Это бот проката эндуро-мотоциклов. Здесь можно записаться "
    "на выезд, посмотреть свои поездки и узнать всё о прокате.\n\n"
    "Выберите раздел:"
)


def greeting(user: User) -> str:
    """Текст главного меню. Ширину добавляет screen при показе."""
    name = (user.full_name or "").split()[0] if user.full_name else "райдер"
    return GREETING.format(name=name)


@router.message(CommandStart())
async def cmd_start(
    message: Message, user: User, state: FSMContext, bot: Bot
) -> None:
    await screen.replace(bot, message.chat.id, greeting(user), main_menu(user))
    # Сама команда в чате не нужна — остаётся только экран
    await drop_message(bot, message.chat.id, message.message_id)
    await state.set_state(None)


@router.message(Command("menu"))
async def cmd_menu(
    message: Message, user: User, state: FSMContext, bot: Bot
) -> None:
    await screen.replace(bot, message.chat.id, greeting(user), main_menu(user))
    await drop_message(bot, message.chat.id, message.message_id)
    await state.set_state(None)


@router.callback_query(MenuCB.filter(F.action == "home"))
async def back_home(
    call: CallbackQuery, user: User, state: FSMContext, bot: Bot
) -> None:
    await screen.show(call, greeting(user), main_menu(user))
    await state.set_state(None)
    await call.answer()


@router.message(Command("help"))
async def cmd_help(message: Message, user: User, bot: Bot) -> None:
    # Справка встаёт на место экрана, а не вторым меню под ним
    await screen.replace(
        bot,
        message.chat.id,
        "<b>Что умеет бот</b>\n\n"
        "/start — главное меню\n"
        "/menu — вернуться в меню\n\n"
        "Записаться на выезд, посмотреть свои записи и статистику, "
        "почитать о прокате — всё через кнопки меню.\n\n"
        "Если что-то не работает, напишите администратору.",
        main_menu(user),
    )
    await drop_message(bot, message.chat.id, message.message_id)
