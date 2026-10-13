"""Состояния админских форм."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class FleetForm(StatesGroup):
    """Добавление мотоцикла."""

    title = State()
    model = State()
    reg_number = State()
    category = State()
    limits = State()
    confirm = State()


class MaintenanceForm(StatesGroup):
    """Вывод техники на обслуживание."""

    period = State()
    reason = State()
    confirm = State()


class GroupForm(StatesGroup):
    """Пошаговое внесение группы админом.

    Самая длинная форма в боте: дата, время, длительность, затем
    по кругу участники, затем цена и подтверждение.
    """

    datetime = State()
    duration = State()
    participant_search = State()
    participant_recall = State()   # прошлые данные — «Взять их?»
    participant_params = State()
    participant_more = State()
    meeting_point = State()        # на деле — телефон ответственного
    price = State()
    confirm = State()


class BroadcastForm(StatesGroup):
    target = State()
    pick_user = State()
    text = State()
    confirm = State()


class InfoForm(StatesGroup):
    body = State()
    media = State()


class StaffForm(StatesGroup):
    search = State()
    role = State()


class ClientForm(StatesGroup):
    """Поиск клиента и правка его карточки."""

    search = State()
    phone = State()
    note = State()


class MoveForm(StatesGroup):
    """Перенос записи на другое время."""

    datetime = State()


class PhotoForm(StatesGroup):
    """Загрузка фото клиенту: сначала выбор получателя, затем снимки."""

    search = State()
    upload = State()
