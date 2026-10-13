"""Состояния оформления заявки клиентом.

Порядок шагов: дата → время → сколько человек → длительность →
телеграм-аккаунты товарищей (можно пропустить) → параметры каждого →
телефон ответственного → подтверждение.

Записывается один человек, он же главный по группе. Аккаунты
остальных необязательны: приезжают часто компанией, и заставлять
всех заводить бота — лишний барьер.
"""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class BookingFlow(StatesGroup):
    day = State()
    slot = State()
    seats = State()
    duration = State()
    duration_custom = State()
    usernames = State()
    rider_who = State()      # кто: прошлый товарищ, ФИО или @ник
    rider_recall = State()   # прошлые данные — «Всё так?»
    rider_params = State()
    rider_experience = State()
    phone = State()
    confirm = State()
