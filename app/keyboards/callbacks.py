"""Фабрики callback_data.

Telegram ограничивает callback_data 64 байтами, поэтому префиксы
короткие. Проверка длины — в тестах модуля, чтобы разрастание
не вылезло уже в бою молчаливым отказом кнопки.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


#: Заглушка для необязательных строковых полей. Пустая строка в конце
#: callback_data даёт «хвост» вида "f:add:0:" — некоторые связки
#: Telegram и aiogram такой хвост не разбирают, и кнопка молча
#: перестаёт работать. Непустое значение снимает этот риск.
EMPTY = "-"

class MenuCB(CallbackData, prefix="m"):
    """Навигация по главному меню."""

    action: str  # book | cabinet | info | admin | home


class DurationCB(CallbackData, prefix="d"):
    minutes: int


class CalendarCB(CallbackData, prefix="c"):
    """Инлайн-календарь.

    action: nav — переключить месяц, day — выбрать день,
            skip — неактивная ячейка, back — шаг назад
    """

    action: str
    year: int = 0
    month: int = 0
    day: int = 0


class SlotCB(CallbackData, prefix="s"):
    """Выбор времени. Дата передаётся как YYYYMMDD, время как HHMM."""

    date: str
    time: str


class SeatsCB(CallbackData, prefix="n"):
    count: int


class ExpCB(CallbackData, prefix="e"):
    """Уровень подготовки участника.

    Отдельный класс, а не число внутри SeatsCB: иначе выбор опыта
    перехватывается обработчиком количества людей, и форма встаёт.
    """

    level: int


class ConfirmCB(CallbackData, prefix="k"):
    action: str  # yes | no | skip | same | edit | phone


class CompanionCB(CallbackData, prefix="cp"):
    """Выбор товарища, которого клиент уже привозил.

    В кнопке только номер в списке: сам список лежит в FSM, так
    в callback_data не попадают ни id, ни имена.
    """

    index: int


class BookingCB(CallbackData, prefix="b"):
    """Действия над конкретной записью."""

    action: str  # view | cancel | confirm_cancel
    booking_id: int


class AdmCalCB(CallbackData, prefix="ac"):
    """Календарь записей в админке.

    action: month — показать месяц, day — записи дня,
            purge — спросить про удаление отменённых (d=0 — за месяц),
            purge_ok — удалить, skip — пустая ячейка.
    """

    action: str
    y: int = 0
    m: int = 0
    d: int = 0


class StatsCB(CallbackData, prefix="sm"):
    """Статистика за выбранный месяц — листание ‹ ›."""

    year: int
    month: int


class AdminCB(CallbackData, prefix="a"):
    """Разделы админки."""

    action: str  # home | fleet | bookings | broadcast | info | stats | export | staff


class FleetCB(CallbackData, prefix="f"):
    """Действия над техникой.

    action: view | add | status | set_status | retire | maint | maint_del
    """

    action: str
    moto_id: int = 0
    value: str = EMPTY


class AdmBookingCB(CallbackData, prefix="ab"):
    """Работа админа с записями.

    action: list | view | confirm | reject | cancel | complete | noshow |
            paid | reassign | set_moto | add_group
    """

    action: str
    booking_id: int = 0
    value: int = 0


class BroadcastCB(CallbackData, prefix="bc"):
    """Рассылки. target: all | booking | user"""

    action: str  # start | target | send | cancel
    value: str = EMPTY


class InfoEditCB(CallbackData, prefix="ie"):
    action: str  # pick | text | media | clear
    section_id: int = 0


class StaffCB(CallbackData, prefix="st"):
    action: str  # list | add | remove | set_role
    user_id: int = 0
    value: str = EMPTY


class ClientCB(CallbackData, prefix="cl"):
    """Карточка клиента в админке.

    action: view | bookings | photos | block | phone | note | delphoto
    """

    action: str
    user_id: int = 0
    value: int = 0


class PageCB(CallbackData, prefix="p"):
    """Пагинация длинных списков."""

    scope: str
    page: int
    value: str = EMPTY


class InfoCB(CallbackData, prefix="i"):
    """Раздел передаём числовым id, а не слагом.

    Слаг задаёт админ, и длинное кириллическое название вылезает
    за лимит в 64 байта — кнопка тогда молча перестаёт работать.
    """

    section_id: int
