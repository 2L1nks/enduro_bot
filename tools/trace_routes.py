#!/usr/bin/env python3
"""Симулятор маршрутизации aiogram на основе разбора кода.

Отвечает на вопрос «какой хендлер получит это нажатие или это сообщение
в таком-то состоянии FSM» — не исполняя код. Нужен, потому что
«кнопка не работает» почти всегда означает, что ни один хендлер
не подошёл, а найти это чтением глазами тяжело.

Запуск:  python tools/trace_routes.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"


@dataclass
class Handler:
    module: str
    name: str
    event: str                    # message | callback_query
    cb_class: str | None = None   # на какой CallbackData фильтруется
    cb_action: str | None = None  # требуемое значение action
    #: Любые условия вида ``F.поле == значение`` из фильтра.
    #: Фильтруются не только по action: PageCB разбирается по scope,
    #: DurationCB — по minutes, и симулятор должен это учитывать,
    #: иначе хендлер с узким фильтром притворится универсальным.
    cb_fields: dict[str, str] = field(default_factory=dict)
    states: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    content: list[str] = field(default_factory=list)  # F.photo и подобное
    order: int = 0


def parse_handlers() -> dict[str, list[Handler]]:
    """Хендлеры по модулям, в порядке регистрации."""
    result: dict[str, list[Handler]] = {}

    for path in sorted((APP / "handlers").rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = f"{path.parent.name}/{path.stem}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        handlers: list[Handler] = []

        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                src = ast.unparse(dec)
                if not src.startswith("router."):
                    continue
                event = (
                    "callback_query"
                    if "router.callback_query" in src
                    else "message"
                    if "router.message" in src
                    else None
                )
                if event is None:
                    continue

                h = Handler(module=module, name=node.name, event=event,
                            order=len(handlers))

                cb = re.search(r"(\w+CB)\.filter\(", src)
                if cb:
                    h.cb_class = cb.group(1)
                    act = re.search(r"F\.action == '([^']+)'", src)
                    h.cb_action = act.group(1) if act else "*"
                    # Строки в кавычках и числа без них
                    for m in re.finditer(
                        r"F\.(\w+) == (?:'([^']*)'|(\d+))", src
                    ):
                        name = m.group(1)
                        h.cb_fields[name] = (
                            m.group(2) if m.group(2) is not None else m.group(3)
                        )

                h.states = re.findall(r"\b(\w+(?:Form|Flow))\.(\w+)\b", src)
                h.states = [f"{a}.{b}" for a, b in
                            re.findall(r"\b(\w+(?:Form|Flow))\.(\w+)\b", src)]
                h.commands = re.findall(r"Command\('(\w+)'\)", src)
                if "CommandStart()" in src:
                    h.commands.append("start")
                h.content = re.findall(r"F\.(photo|video|text)", src)

                handlers.append(h)

        if handlers:
            result[module] = handlers
    return result


def parse_router_order() -> list[tuple[str, list[str]]]:
    """Порядок включения роутеров и роли, которые их охраняют."""
    admin_init = (APP / "handlers" / "admin" / "__init__.py").read_text(encoding="utf-8")
    main_init = (APP / "handlers" / "__init__.py").read_text(encoding="utf-8")

    groups: list[tuple[str, list[str]]] = []
    for m in re.finditer(
        r"(\w+_router) = Router\(name=\"[\w]+\"\)(.*?)_guard\(\1,([^)]*)\)",
        admin_init,
        re.S,
    ):
        var, body, roles = m.groups()
        mods = re.findall(r"include_router\((\w+)\.router\)", body)
        role_list = [r.strip().split(".")[-1] for r in roles.split(",") if r.strip()]
        groups.append((var, mods, role_list))

    ordered: list[tuple[str, list[str]]] = []
    # Порядок включения внутри build_admin_router
    tail = admin_init[admin_init.index("router.include_router(staff_router)"):]
    for var in re.findall(r"router\.include_router\((\w+_router)\)", tail):
        for g_var, mods, roles in groups:
            if g_var == var:
                for mod in mods:
                    ordered.append((f"admin/{mod}", roles))

    user_mods = re.findall(
        r"router\.include_router\((\w+)\.router\)",
        main_init[main_init.index("def build_user_router"):],
    )
    for mod in user_mods:
        ordered.append((f"user/{mod}", ["*"]))
    # Перехватчик включается последним и ловит всё непойманное
    ordered.append(("common/fallback", ["*"]))
    return ordered


def match(h: Handler, *, event: str, cb_class: str | None, action: str | None,
          state: str | None, command: str | None, content: str | None,
          fields: dict[str, str] | None = None) -> bool:
    if h.event != event:
        return False
    if h.commands and command not in h.commands:
        return False
    if not h.commands and command is not None:
        # Команда попадёт только в хендлер с Command-фильтром
        return False
    if h.cb_class:
        if h.cb_class != cb_class:
            return False
        if h.cb_action not in ("*", action):
            return False
        # Узкий фильтр по другому полю: PageCB(scope=...),
        # DurationCB(minutes=0) и прочие развилки внутри одного класса
        given = dict(fields or {})
        if action is not None:
            given.setdefault("action", action)
        for name, want in h.cb_fields.items():
            if name == "action":
                continue
            got = given.get(name)
            if got is None or str(got) != str(want):
                return False
    elif event == "callback_query":
        return False
    if h.states and state not in h.states:
        return False
    if not h.states and state is not None and event == "message":
        # Сообщение в состоянии FSM: хендлер без фильтра состояния
        # его тоже поймает, если дойдёт очередь
        pass
    if h.content and content not in h.content:
        return False
    return True


def trace(modules: dict[str, list[Handler]], order: list[tuple[str, list[str]]],
          *, event: str, cb_class=None, action=None, state=None,
          command=None, content=None, role="OWNER", **fields) -> Handler | None:
    """Кто первым поймает событие.

    Лишние именованные аргументы — это поля callback_data: ``scope="bk"``,
    ``minutes=90``. Так сценарий описывает нажатие целиком, а не только
    его action.
    """
    for module, roles in order:
        if roles != ["*"] and role not in roles:
            continue
        for h in modules.get(module, []):
            if match(h, event=event, cb_class=cb_class, action=action,
                     state=state, command=command, content=content,
                     fields=fields):
                return h
    return None


SCENARIOS = [
    # (описание, параметры, ожидаем ли попадание)
    ("Админ жмёт «Парк»",
     dict(event="callback_query", cb_class="AdminCB", action="fleet")),
    ("Админ жмёт «Добавить мотоцикл»",
     dict(event="callback_query", cb_class="FleetCB", action="add")),
    ("Админ вводит название мотоцикла",
     dict(event="message", state="FleetForm.title")),
    ("Админ вводит модель",
     dict(event="message", state="FleetForm.model")),
    ("Админ вводит госномер",
     dict(event="message", state="FleetForm.reg_number")),
    ("Админ выбирает категорию",
     dict(event="callback_query", cb_class="FleetCB", action="cat",
          state="FleetForm.category")),
    ("Админ вводит ограничения",
     dict(event="message", state="FleetForm.limits")),

    ("Админ жмёт «Рассылка»",
     dict(event="callback_query", cb_class="AdminCB", action="broadcast")),
    ("Админ выбирает аудиторию",
     dict(event="callback_query", cb_class="BroadcastCB", action="target",
          state="BroadcastForm.target")),
    ("Админ вводит текст рассылки",
     dict(event="message", state="BroadcastForm.text")),
    ("Админ подтверждает отправку",
     dict(event="callback_query", cb_class="BroadcastCB", action="send",
          state="BroadcastForm.confirm")),

    ("Админ жмёт «Записи»",
     dict(event="callback_query", cb_class="AdminCB", action="bookings")),
    ("Админ жмёт «Внести группу»",
     dict(event="callback_query", cb_class="AdmBookingCB", action="add_group")),
    ("Админ вводит дату выезда",
     dict(event="message", state="GroupForm.datetime")),
    ("Админ ищет участника",
     dict(event="message", state="GroupForm.participant_search")),

    ("Админ жмёт «Инфо-раздел»",
     dict(event="callback_query", cb_class="AdminCB", action="info")),
    ("Админ вводит новый текст раздела",
     dict(event="message", state="InfoForm.body")),
    ("Админ жмёт «Статистика»",
     dict(event="callback_query", cb_class="AdminCB", action="stats")),
    ("Админ жмёт «Экспорт»",
     dict(event="callback_query", cb_class="AdminCB", action="export")),
    ("Админ жмёт «Фото клиентам»",
     dict(event="callback_query", cb_class="AdminCB", action="photos")),
    ("Админ ищет клиента для фото",
     dict(event="message", state="PhotoForm.search")),
    ("Админ присылает фото",
     dict(event="message", state="PhotoForm.upload", content="photo")),
    ("Владелец жмёт «Сотрудники»",
     dict(event="callback_query", cb_class="AdminCB", action="staff")),

    ("Клиент жмёт «Записаться»",
     dict(event="callback_query", cb_class="MenuCB", action="book", role="USER")),
    ("Клиент жмёт «Мои фото»",
     dict(event="callback_query", cb_class="MenuCB", action="photos", role="USER")),
    ("Клиент жмёт «О прокате»",
     dict(event="callback_query", cb_class="MenuCB", action="info", role="USER")),
    # --- добавленное в аудите ---
    ("Админ возвращает мотоцикл в парк",
     dict(event="callback_query", cb_class="FleetCB", action="restore")),
    ("Админ удаляет мотоцикл совсем",
     dict(event="callback_query", cb_class="FleetCB", action="delete")),
    ("Админ снимает простой",
     dict(event="callback_query", cb_class="FleetCB", action="maint_del")),
    ("Админ жмёт «Перенести»",
     dict(event="callback_query", cb_class="AdmBookingCB", action="move")),
    ("Админ вводит новое время переноса",
     dict(event="message", state="MoveForm.datetime")),
    ("Админ жмёт «Клиенты»",
     dict(event="callback_query", cb_class="AdminCB", action="clients")),
    ("Админ ищет клиента",
     dict(event="message", state="ClientForm.search")),
    ("Админ открывает карточку клиента",
     dict(event="callback_query", cb_class="ClientCB", action="view")),
    ("Админ блокирует клиента",
     dict(event="callback_query", cb_class="ClientCB", action="block")),
    ("Админ вводит телефон клиента",
     dict(event="message", state="ClientForm.phone")),
    ("Админ вводит заметку о клиенте",
     dict(event="message", state="ClientForm.note")),
    ("Админ удаляет фото клиента",
     dict(event="callback_query", cb_class="ClientCB", action="delphoto")),
    ("Админ выбирает «Одному клиенту»",
     dict(event="callback_query", cb_class="BroadcastCB", action="target",
          state="BroadcastForm.target")),
    ("Админ ищет получателя рассылки",
     dict(event="message", state="BroadcastForm.pick_user")),
    ("Админ выбрал получателя",
     dict(event="callback_query", cb_class="BroadcastCB", action="one",
          state="BroadcastForm.pick_user")),

    # --- оплата: экран со списком и отметка ---
    ("Админ открывает экран оплаты",
     dict(event="callback_query", cb_class="AdmBookingCB", action="paid")),
    ("Админ отмечает оплату",
     dict(event="callback_query", cb_class="AdmBookingCB", action="paid_ok")),
    ("Админ открывает запись",
     dict(event="callback_query", cb_class="AdmBookingCB", action="view")),
    ("Админ листает записи",
     dict(event="callback_query", cb_class="PageCB", scope="bk")),

    # --- запись клиента: шаг за шагом ---
    ("Клиент листает календарь",
     dict(event="callback_query", cb_class="CalendarCB", action="nav",
          role="USER")),
    ("Клиент выбирает день",
     dict(event="callback_query", cb_class="CalendarCB", action="day",
          role="USER")),
    ("Клиент выбирает время",
     dict(event="callback_query", cb_class="SlotCB", role="USER")),
    ("Клиент выбирает число человек",
     dict(event="callback_query", cb_class="SeatsCB", role="USER",
          state="BookingFlow.seats")),
    ("Клиент выбирает длительность",
     dict(event="callback_query", cb_class="DurationCB", role="USER",
          minutes=90, state="BookingFlow.duration")),
    ("Клиент жмёт «Своя длительность»",
     dict(event="callback_query", cb_class="DurationCB", role="USER",
          minutes=0, state="BookingFlow.duration")),
    ("Клиент вводит свою длительность",
     dict(event="message", role="USER", state="BookingFlow.duration_custom")),
    ("Клиент пропускает телеграмы товарищей",
     dict(event="callback_query", cb_class="ConfirmCB", action="skip",
          role="USER", state="BookingFlow.usernames")),
    ("Клиент вводит телеграмы товарищей",
     dict(event="message", role="USER", state="BookingFlow.usernames")),
    ("Клиент вводит рост/вес/обувь",
     dict(event="message", role="USER", state="BookingFlow.rider_params")),
    ("Клиент выбирает опыт езды",
     dict(event="callback_query", cb_class="ExpCB", role="USER",
          state="BookingFlow.rider_experience")),
    ("Клиент вводит телефон",
     dict(event="message", role="USER", state="BookingFlow.phone")),
    ("Клиент подтверждает запись",
     dict(event="callback_query", cb_class="ConfirmCB", action="yes",
          role="USER", state="BookingFlow.confirm")),

    # --- статистика по месяцам и защита закрытия выезда ---
    ("Админ листает статистику по месяцам",
     dict(event="callback_query", cb_class="StatsCB", year=2026, month=9)),
    ("Админ жмёт «Завершить» выезд",
     dict(event="callback_query", cb_class="AdmBookingCB", action="complete")),
    ("Админ жмёт «Не пришёл»",
     dict(event="callback_query", cb_class="AdmBookingCB", action="noshow")),
    ("Клиент вызывает /help",
     dict(event="message", command="help", role="USER")),
    ("Админ вызывает /admin",
     dict(event="message", command="admin")),

    # --- календарь записей и удаление отменённых ---
    ("Админ открывает календарь записей",
     dict(event="callback_query", cb_class="AdminCB", action="bookings")),
    ("Админ листает месяц календаря",
     dict(event="callback_query", cb_class="AdmCalCB", action="month")),
    ("Админ жмёт на дату",
     dict(event="callback_query", cb_class="AdmCalCB", action="day")),
    ("Админ жмёт пустую ячейку календаря",
     dict(event="callback_query", cb_class="AdmCalCB", action="skip")),
    ("Админ жмёт «Удалить отменённые»",
     dict(event="callback_query", cb_class="AdmCalCB", action="purge")),
    ("Админ подтверждает удаление",
     dict(event="callback_query", cb_class="AdmCalCB", action="purge_ok")),
    ("Админ вносит группу с даты календаря",
     dict(event="callback_query", cb_class="AdmBookingCB", action="add_group")),

    # --- админ: аккаунт по ФИО и прошлые данные ---
    ("Админ заводит человека без телеграма",
     dict(event="callback_query", cb_class="AdmBookingCB", action="grp_new",
          state="GroupForm.participant_search")),
    ("Админ берёт прошлые данные",
     dict(event="callback_query", cb_class="AdmBookingCB", action="grp_same",
          state="GroupForm.participant_recall")),
    ("Админ вводит данные заново",
     dict(event="callback_query", cb_class="AdmBookingCB", action="grp_edit",
          state="GroupForm.participant_recall")),
    ("Админ вводит рост/вес/обувь участника",
     dict(event="message", state="GroupForm.participant_params")),
    ("Админ выбирает опыт участника",
     dict(event="callback_query", cb_class="AdmBookingCB", action="grp_exp",
          state="GroupForm.participant_params")),
    ("Админ оставляет прежний телефон",
     dict(event="callback_query", cb_class="AdmBookingCB", action="grp_phone",
          state="GroupForm.meeting_point")),
    ("Админ вносит без телефона",
     dict(event="callback_query", cb_class="AdmBookingCB", action="grp_nophone",
          state="GroupForm.meeting_point")),
    ("Админ вводит телефон",
     dict(event="message", state="GroupForm.meeting_point")),
    ("Админ сохраняет группу",
     dict(event="callback_query", cb_class="AdmBookingCB", action="grp_save",
          state="GroupForm.confirm")),

    # --- клиент: прошлые товарищи и «Всё так» ---
    ("Клиент выбирает прошлого товарища",
     dict(event="callback_query", cb_class="CompanionCB", role="USER",
          state="BookingFlow.rider_who")),
    ("Клиент пишет ФИО товарища",
     dict(event="message", role="USER", state="BookingFlow.rider_who")),
    ("Клиент жмёт «Всё так»",
     dict(event="callback_query", cb_class="ConfirmCB", action="same",
          role="USER", state="BookingFlow.rider_recall")),
    ("Клиент жмёт «Изменить»",
     dict(event="callback_query", cb_class="ConfirmCB", action="edit",
          role="USER", state="BookingFlow.rider_recall")),
    ("Клиент оставляет прежний телефон",
     dict(event="callback_query", cb_class="ConfirmCB", action="phone",
          role="USER", state="BookingFlow.phone")),

    # --- кабинет: то, что падало с «Что-то пошло не так» ---
    ("Клиент жмёт «Мои записи»",
     dict(event="callback_query", cb_class="MenuCB", action="cabinet",
          role="USER")),
    ("Клиент жмёт «История»",
     dict(event="callback_query", cb_class="MenuCB", action="history",
          role="USER")),
    ("Клиент открывает свою запись",
     dict(event="callback_query", cb_class="BookingCB", action="view",
          role="USER")),
    ("Клиент отменяет запись",
     dict(event="callback_query", cb_class="BookingCB", action="do_cancel",
          role="USER")),
]



def main() -> int:
    modules = parse_handlers()
    order = parse_router_order()

    print("Порядок роутеров:")
    for mod, roles in order:
        print(f"  {mod:12s} роли: {','.join(roles)}")
    print()

    broken = 0
    print("Маршруты:")
    for label, params in SCENARIOS:
        hit = trace(modules, order, **params)
        if hit is None:
            print(f"  ✖    {label:42s} → НИКТО НЕ ОБРАБОТАЕТ")
            broken += 1
        elif hit.module == "common/fallback":
            # Дошло до перехватчика — значит нужный обработчик не сработал
            print(f"  ✖    {label:42s} → ПРОВАЛИЛОСЬ В ПЕРЕХВАТЧИК")
            broken += 1
        else:
            print(f"  OK   {label:42s} → {hit.module}.{hit.name}")

    # Владелец часто проверяет клиентскую запись со своего аккаунта.
    # Для него админские роутеры стоят первыми и не должны перехватить
    # ни одного клиентского шага.
    print("\nКлиентские шаги от имени владельца:")
    owner_broken = 0
    for label, params in SCENARIOS:
        if params.get("role") != "USER":
            continue
        as_user = trace(modules, order, **params)
        as_owner = trace(modules, order, **dict(params, role="OWNER"))
        if as_user and as_owner and (as_user.module, as_user.name) == (
            as_owner.module, as_owner.name
        ):
            continue
        where = f"{as_owner.module}.{as_owner.name}" if as_owner else "никуда"
        print(f"  ✖    {label:42s} → у владельца уходит в {where}")
        owner_broken += 1
    if not owner_broken:
        print("  OK   админка не перехватывает ни одного клиентского шага")
    broken += owner_broken

    print()
    if broken:
        print(f"Недостижимых сценариев: {broken}")
        return 1
    print("Все сценарии достижимы.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
