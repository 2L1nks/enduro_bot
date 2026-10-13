#!/usr/bin/env python3
"""Статический анализ проекта без установки зависимостей.

Ловит то, что иначе обнаруживается только при запуске:
импорты несуществующих имён, обращения к отсутствующим атрибутам
модулей, мёртвые кнопки, превышение лимита callback_data,
зависимости хендлеров, которых не даёт middleware.

Запуск:  python tools/check_project.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app"

#: Что middleware кладёт в data + собственные аргументы aiogram
PROVIDED_ARGS = {
    "session", "user", "state", "bot", "callback_data", "event_from_user",
    "dispatcher", "call", "message", "event", "command",
}

CALLBACK_LIMIT = 64

problems: list[str] = []
checks_run = 0


def fail(message: str) -> None:
    problems.append(message)


def py_files() -> list[pathlib.Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    return ".".join(parts[:-1]) if parts[-1] == "__init__" else ".".join(parts)


def top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.Try):
            # Импорты в try/except ImportError — тоже имена модуля
            for sub in node.body:
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    names |= {a.asname or a.name.split(".")[0] for a in sub.names}
    return names


def check_syntax() -> dict[str, ast.Module]:
    """Разбираем все файлы. Без этого остальные проверки бессмысленны."""
    global checks_run
    checks_run += 1
    trees: dict[str, ast.Module] = {}
    for path in py_files():
        try:
            trees[module_name(path)] = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
        except SyntaxError as exc:
            fail(f"{path.relative_to(ROOT)}:{exc.lineno} синтаксис — {exc.msg}")
    return trees


def check_imports(trees: dict[str, ast.Module]) -> None:
    """Каждое имя, импортируемое из app.*, должно существовать."""
    global checks_run
    checks_run += 1
    defined = {mod: top_level_names(tree) for mod, tree in trees.items()}

    for mod, tree in trees.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            if not node.module.startswith("app"):
                continue
            if node.module not in defined:
                fail(f"{mod}: импорт из несуществующего модуля {node.module}")
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name in defined[node.module]:
                    continue
                # Имя может быть подмодулем пакета
                if f"{node.module}.{alias.name}" in defined:
                    continue
                fail(f"{mod}: в {node.module} нет имени '{alias.name}'")


def check_module_attrs(trees: dict[str, ast.Module]) -> None:
    """Обращения вида repo.some_function — функция должна быть."""
    global checks_run
    checks_run += 1
    defined = {mod: top_level_names(tree) for mod, tree in trees.items()}

    for mod, tree in trees.items():
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if full in defined:
                        aliases[alias.asname or alias.name] = full
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in defined:
                        aliases[alias.asname or alias.name] = alias.name

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
                continue
            target = aliases.get(node.value.id)
            if target and node.attr not in defined[target]:
                fail(f"{mod}: {node.value.id}.{node.attr} — в {target} такого нет")


def check_handler_args(trees: dict[str, ast.Module]) -> None:
    """Хендлер не должен просить то, чего middleware не даёт."""
    global checks_run
    checks_run += 1
    for mod, tree in trees.items():
        if ".handlers." not in mod:
            continue
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            decorators = " ".join(ast.unparse(d) for d in node.decorator_list)
            if "router." not in decorators:
                continue
            for arg in node.args.args:
                if arg.arg not in PROVIDED_ARGS:
                    fail(
                        f"{mod}:{node.name} просит аргумент '{arg.arg}', "
                        f"которого нет в middleware"
                    )


def check_callback_sizes() -> None:
    """callback_data должен влезать в 64 байта.

    Считаем по фактическим литералам из кода: гипотетический худший
    случай («а вдруг туда положат 40 символов») дал бы ложные срабатывания
    на каждом строковом поле. Отдельно предупреждаем, если в поле кладут
    не литерал, а переменную — там длину статически не узнать.
    """
    global checks_run
    checks_run += 1

    callbacks_path = APP / "keyboards" / "callbacks.py"
    tree = ast.parse(callbacks_path.read_text(encoding="utf-8"))

    # Собираем фактические аргументы вызовов: {класс: {поле: {литералы}}}
    # и отдельно поля, в которые кладут выражение, а не литерал.
    literals_by_class: dict[str, dict[str, set[str]]] = {}
    dynamic_by_class: dict[str, set[str]] = {}

    for path in list((APP / "keyboards").rglob("*.py")) + list(
        (APP / "handlers").rglob("*.py")
    ):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            cls_name = node.func.id
            if not cls_name.endswith("CB"):
                continue
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(
                    kw.value.value, str
                ):
                    literals_by_class.setdefault(cls_name, {}).setdefault(
                        kw.arg, set()
                    ).add(kw.value.value)
                elif not isinstance(kw.value, ast.Constant):
                    dynamic_by_class.setdefault(cls_name, set()).add(kw.arg)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        prefix = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant)
            ),
            None,
        )
        if prefix is None:
            continue
        cls = node.name

        # Только настоящие поля: аннотированные присваивания в теле класса
        fields = [
            (sub.target.id, ast.unparse(sub.annotation))
            for sub in node.body
            if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name)
        ]
        if not fields:
            continue

        # Самое длинное значение каждого поля среди встреченных в коде
        worst = [prefix]
        str_dynamic: list[str] = []
        class_literals = literals_by_class.get(cls, {})
        class_dynamic = dynamic_by_class.get(cls, set())

        for fname, ftype in fields:
            if ftype == "int":
                # 12 цифр с запасом: Telegram ID — 10 цифр, id записей
                # в этом проекте столько не наберут
                worst.append("9" * 12)
                continue
            seen = class_literals.get(fname, set())
            worst.append(max(seen, key=lambda s: len(s.encode())) if seen else "")
            if fname in class_dynamic:
                str_dynamic.append(fname)

        size = len(":".join(worst).encode("utf-8"))
        if size > CALLBACK_LIMIT:
            fail(f"callbacks: {cls} даёт {size} байт > {CALLBACK_LIMIT}")
        elif str_dynamic and CALLBACK_LIMIT - size < 20:
            # В строковое поле кладут выражение — длину статически не узнать
            fail(
                f"callbacks: {cls} — поля {str_dynamic} заполняются выражением, "
                f"а запас всего {CALLBACK_LIMIT - size} байт"
            )


def check_dead_buttons() -> None:
    """Каждое значение action, попадающее в кнопку, должно обрабатываться."""
    global checks_run
    checks_run += 1

    kb_source = "\n".join(
        p.read_text(encoding="utf-8") for p in (APP / "keyboards").rglob("*.py")
    )
    handlers_source = "\n".join(
        p.read_text(encoding="utf-8") for p in (APP / "handlers").rglob("*.py")
    )
    everything = kb_source + "\n" + handlers_source

    cb_classes = re.findall(
        r"class (\w+)\(CallbackData",
        (APP / "keyboards" / "callbacks.py").read_text(encoding="utf-8"),
    )

    for cls in cb_classes:
        produced = set(
            re.findall(rf'{cls}\(\s*\n?\s*action="(\w+)"', everything)
        )
        handled = set(
            re.findall(rf'{cls}\.filter\(F\.action == "(\w+)"\)', handlers_source)
        )
        # Фильтр без action ловит все значения этого класса
        catch_all = bool(re.search(rf"{cls}\.filter\(\)", handlers_source))

        if catch_all:
            continue
        for action in sorted(produced - handled):
            fail(f"мёртвая кнопка: {cls}(action='{action}') никто не обрабатывает")


def check_fsm_states(trees: dict[str, ast.Module]) -> None:
    """Состояния, упомянутые в хендлерах, должны быть объявлены."""
    global checks_run
    checks_run += 1

    declared: set[str] = set()
    for mod, tree in trees.items():
        if ".states." not in mod:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for sub in node.body:
                if isinstance(sub, ast.Assign):
                    for target in sub.targets:
                        if isinstance(target, ast.Name):
                            declared.add(f"{node.name}.{target.id}")

    handlers_source = "\n".join(
        p.read_text(encoding="utf-8") for p in (APP / "handlers").rglob("*.py")
    )
    for used in set(re.findall(r"\b([A-Z]\w*Form|BookingFlow)\.(\w+)", handlers_source)):
        full = f"{used[0]}.{used[1]}"
        if used[1] in ("filter", "state", "set_state"):
            continue
        if full not in declared:
            fail(f"FSM: состояние {full} используется, но не объявлено")


def check_router_wiring(trees: dict[str, ast.Module]) -> None:
    """Каждый модуль с router должен быть включён в сборку."""
    global checks_run
    checks_run += 1

    with_router: set[str] = set()
    for path in (APP / "handlers").rglob("*.py"):
        if path.name == "__init__.py":
            continue
        if "router = Router(" in path.read_text(encoding="utf-8"):
            with_router.add(path.stem)
    wiring = (
        (APP / "handlers" / "__init__.py").read_text(encoding="utf-8")
        + (APP / "handlers" / "admin" / "__init__.py").read_text(encoding="utf-8")
    )
    for name in sorted(with_router):
        if f"{name}.router" not in wiring:
            fail(f"роутер {name}.router нигде не включён — его хендлеры мертвы")


def check_call_signatures(trees: dict[str, ast.Module]) -> None:
    """Вызовы функций проекта должны совпадать с их сигнатурой.

    Передать несуществующий именованный аргумент — обычная опечатка
    при рефакторинге, а видно её только когда пользователь жмёт кнопку
    и получает «Что-то пошло не так».
    """
    global checks_run
    checks_run += 1

    # Сигнатуры публичных функций проекта
    signatures: dict[str, tuple[list[str], bool, int]] = {}
    for mod, tree in trees.items():
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
            accepts_kwargs = args.kwarg is not None
            # *args означает «сколько угодно позиционных»
            positional = (
                10_000
                if args.vararg is not None
                else len(args.posonlyargs) + len(args.args)
            )
            signatures[f"{mod}.{node.name}"] = (names, accepts_kwargs, positional)

    for mod, tree in trees.items():
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # вид screen.show(...) или show(...)
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                target = aliases.get(node.func.value.id)
                full = f"{target}.{node.func.attr}" if target else None
            elif isinstance(node.func, ast.Name):
                full = aliases.get(node.func.id)
            else:
                continue
            if not full or full not in signatures:
                continue

            names, accepts_kwargs, positional = signatures[full]
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if kw.arg not in names and not accepts_kwargs:
                    fail(
                        f"{mod}: {full}() не принимает аргумент '{kw.arg}' "
                        f"(есть: {', '.join(names)})"
                    )
            if len(node.args) > positional and not any(
                isinstance(a, ast.Starred) for a in node.args
            ):
                fail(
                    f"{mod}: {full}() принимает {positional} позиционных "
                    f"аргументов, передано {len(node.args)}"
                )


def check_sql_param_types() -> None:
    """Параметры внутри CAST(...) в сыром SQL должны иметь объявленный тип.

    asyncpg выводит тип параметра из запроса и требует ровно его:
    строка '09:00:00' в CAST(:p AS time) роняет драйвер ещё до базы.
    SQLAlchemy не угадает тип за нас — нужен bindparam.
    """
    global checks_run
    checks_run += 1

    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r'text\(\s*\n?\s*"""(.*?)"""', source, re.S):
            sql = match.group(1)
            casts = set(re.findall(r"CAST\(:(\w+)\s+AS", sql))
            if not casts:
                continue
            tail = source[match.end() : match.end() + 800]
            declared = set(re.findall(r'bindparam\("(\w+)"', tail))
            for name in sorted(casts - declared):
                fail(
                    f"{path.relative_to(ROOT)}: параметр :{name} внутри CAST "
                    f"без bindparam — драйвер не примет значение"
                )


def check_env_parity(trees: dict[str, ast.Module]) -> None:
    """Поля Settings и строки .env.example должны совпадать."""
    global checks_run
    checks_run += 1

    config_tree = trees.get("app.config")
    if config_tree is None:
        fail("не найден app.config")
        return

    fields: set[str] = set()
    for node in ast.walk(config_tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    fields.add(sub.target.id)

    env_keys = {
        line.split("=")[0].strip().lower()
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    }

    # Поле с validation_alias читается под другим именем
    config_source = (APP / "config.py").read_text(encoding="utf-8")
    aliases = {a.lower() for a in re.findall(r'validation_alias="(\w+)"', config_source)}

    for field in sorted(fields):
        if field in env_keys or field in aliases:
            continue
        if field.endswith("_raw") and field[:-4] in env_keys:
            continue
        fail(f".env.example: нет строки для поля {field}")

    for key in sorted(env_keys - fields - aliases):
        fail(f".env.example: строка {key.upper()} не соответствует ни одному полю")


class _Scope:
    """Область видимости: что в ней связано и что из неё читается."""

    def __init__(self, kind: str, parent: "_Scope | None") -> None:
        self.kind = kind            # module | function | class | comp
        self.parent = parent
        self.bound: set[str] = set()
        self.loads: list[tuple[str, int]] = []
        self.globals: set[str] = set()


class _NameCollector(ast.NodeVisitor):
    """Мини-pyflakes: собирает области видимости и чтения имён.

    Аннотации не обходятся: с ``from __future__ import annotations``
    они не вычисляются, и имена из TYPE_CHECKING там законны.
    """

    def __init__(self) -> None:
        self.module = _Scope("module", None)
        self.scopes: list[_Scope] = [self.module]
        self.current = self.module
        self.has_star = False

    # --- служебное ---
    def _enter(self, kind: str) -> _Scope:
        scope = _Scope(kind, self.current)
        self.scopes.append(scope)
        self.current = scope
        return scope

    def _leave(self, scope: _Scope) -> None:
        self.current = scope.parent  # type: ignore[assignment]

    def _bind(self, name: str) -> None:
        target = self.module if name in self.current.globals else self.current
        target.bound.add(name)

    def _bind_target(self, node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                self._bind(sub.id)
            elif isinstance(sub, ast.Starred) and isinstance(sub.value, ast.Name):
                self._bind(sub.value.id)

    def _args(self, args: ast.arguments) -> None:
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self.current.bound.add(a.arg)
        if args.vararg:
            self.current.bound.add(args.vararg.arg)
        if args.kwarg:
            self.current.bound.add(args.kwarg.arg)

    def _defaults(self, args: ast.arguments) -> None:
        for d in (*args.defaults, *[d for d in args.kw_defaults if d is not None]):
            self.visit(d)

    # --- узлы ---
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.current.loads.append((node.id, node.lineno))
        else:
            self._bind(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self._bind(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for a in node.names:
            if a.name == "*":
                self.has_star = True
            else:
                self._bind(a.asname or a.name)

    def visit_Global(self, node: ast.Global) -> None:
        self.current.globals |= set(node.names)
        self.module.bound |= set(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.current.bound |= set(node.names)

    def _function(self, node) -> None:
        for dec in getattr(node, "decorator_list", ()):
            self.visit(dec)
        self._defaults(node.args)
        if not isinstance(node, ast.Lambda):
            self._bind(node.name)
        scope = self._enter("function")
        self._args(node.args)
        body = node.body if isinstance(node.body, list) else [node.body]
        for stmt in body:
            self.visit(stmt)
        self._leave(scope)

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function
    visit_Lambda = _function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        for base in (*node.bases, *[k.value for k in node.keywords]):
            self.visit(base)
        self._bind(node.name)
        scope = self._enter("class")
        for stmt in node.body:
            self.visit(stmt)
        self._leave(scope)

    def _comprehension(self, node) -> None:
        # Первый итератор вычисляется снаружи
        self.visit(node.generators[0].iter)
        scope = self._enter("comp")
        for i, gen in enumerate(node.generators):
            self._bind_target(gen.target)
            if i:
                self.visit(gen.iter)
            for cond in gen.ifs:
                self.visit(cond)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self._leave(scope)

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._bind(node.name)
        for stmt in node.body:
            self.visit(stmt)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        # Моржовый оператор в генераторе связывает имя снаружи
        scope = self.current
        while scope.kind == "comp" and scope.parent is not None:
            scope = scope.parent
        scope.bound.add(node.target.id)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._bind_target(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_arg(self, node: ast.arg) -> None:  # аннотации аргументов
        return

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._bind(node.name)
        if node.pattern:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._bind(node.rest)
        self.generic_visit(node)


_MODULE_DUNDERS = {
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "__annotations__",
}


def check_undefined_names(trees: dict[str, ast.Module]) -> None:
    """Имена, которых нет ни в функции, ни в модуле, ни во встроенных.

    Такое падает NameError только в момент вызова — например, когда
    обработчик использует сервис, импортированный внутри другой
    функции. Ни компиляция, ни импорт модуля этого не покажут.
    """
    global checks_run
    checks_run += 1

    import builtins

    known = set(dir(builtins)) | _MODULE_DUNDERS

    for mod, tree in trees.items():
        collector = _NameCollector()
        collector.visit(tree)
        if collector.has_star:
            continue

        for scope in collector.scopes:
            for name, line in scope.loads:
                if name in known:
                    continue
                visible = False
                probe: _Scope | None = scope
                first = True
                while probe is not None:
                    # Тело класса не видно из его методов и генераторов
                    if probe.kind == "class" and not first:
                        probe = probe.parent
                        continue
                    if name in probe.bound:
                        visible = True
                        break
                    first = False
                    probe = probe.parent
                if not visible:
                    fail(f"{mod}:{line}: имя '{name}' нигде не определено "
                         "(забыт импорт?)")


def main() -> int:
    print("Проверка проекта\n" + "─" * 50)

    trees = check_syntax()
    if problems:
        print("Синтаксические ошибки — остальные проверки пропущены:")
        for item in problems:
            print("  ✖", item)
        return 1

    check_imports(trees)
    check_module_attrs(trees)
    check_handler_args(trees)
    check_callback_sizes()
    check_dead_buttons()
    check_fsm_states(trees)
    check_router_wiring(trees)
    check_call_signatures(trees)
    check_sql_param_types()
    check_env_parity(trees)
    check_undefined_names(trees)

    print(f"Файлов разобрано: {len(trees)}")
    print(f"Проверок выполнено: {checks_run}")
    print("─" * 50)

    if problems:
        print(f"Найдено проблем: {len(problems)}\n")
        for item in problems:
            print("  ✖", item)
        return 1

    print("Проблем не найдено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
