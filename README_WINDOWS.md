# Запуск на Windows (PostgreSQL + pgAdmin, без Docker)

Инструкция для локальной разработки. Боевой деплой через Docker — в `README.md`.

---

## Шаг 1. Что должно быть установлено

| Что | Где взять | Проверка в PowerShell |
|---|---|---|
| Python 3.11 или новее | python.org — **обязательно отметьте «Add python.exe to PATH»** | `py --version` |
| PostgreSQL 14+ | postgresql.org/download/windows | `psql --version` |
| pgAdmin 4 | ставится вместе с PostgreSQL | — |

Если `py --version` отвечает ошибкой, Python не прописан в PATH: переустановите его с галочкой «Add python.exe to PATH».

---

## Шаг 2. Создать базу в pgAdmin

1. Откройте pgAdmin, подключитесь к серверу (пароль задавали при установке PostgreSQL).
2. В дереве слева: правый клик на **Databases** → **Create** → **Database…**
3. Поле **Database**: `enduro`. Нажмите **Save**.

---

## Шаг 3. Накатить схему

1. Кликните на базу **enduro** в дереве — именно на неё, а не на `postgres`.
2. Меню **Tools** → **Query Tool**.
3. Значок папки на панели (**Open File**) → выберите `docs\schema.sql` из проекта.
4. Нажмите **F5** (или кнопку ▶).

Внизу, на вкладке **Data Output**, должно появиться:

| check | result |
|---|---|
| Таблиц создано (ожидается 13) | 13 |
| Защита от пересечений (ожидается 2) | 2 |
| Разделов инфо (ожидается 7) | 7 |

Если все три цифры совпали — база готова. Скрипт безопасно запускать повторно.

> **Если ошибка `could not open extension control file "btree_gist"`** — при установке PostgreSQL не поставились contrib-модули. Запустите установщик ещё раз и отметьте компонент **Stack Builder / contrib**, либо переустановите PostgreSQL со стандартным набором компонентов.

---

## Шаг 4. Настроить проект

Откройте PowerShell в папке проекта (Shift + правый клик в папке → «Открыть окно PowerShell здесь»):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

> **Если PowerShell ругается на запуск скриптов** при активации venv:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
> и повторите активацию.

Откройте `.env` в блокноте и заполните три строки:

```ini
BOT_TOKEN=токен от @BotFather
OWNER_TG_ID=ваш ID от @userinfobot
POSTGRES_PASSWORD=пароль от PostgreSQL
```

Остальное для локального запуска менять не нужно: `FSM_STORAGE=memory` и `STORAGE_BACKEND=local` работают без Redis и MinIO.

---

## Шаг 5. Запустить

```powershell
python -m app.main
```

Ожидаемый вывод:

```
INFO  [app.main] Запуск бота
INFO  [app.main] База данных доступна, btree_gist на месте
INFO  [app.db.bootstrap] Создан владелец с tg_id=...
INFO  [app.db.bootstrap] Инфо-разделы проверены
WARNING [app.main] FSM: память — незаконченные формы потеряются при перезапуске...
INFO  [app.main] Бот @ваш_бот готов к работе
```

Предупреждение про память — ожидаемое, для разработки так и задумано.

Остановить: **Ctrl + C**.

На этом шаге бот только стартует и проверяет связность — команд он пока не отвечает, они появятся в следующих модулях.

---

## Шаг 6. Проверить, что защита от двойного бронирования работает

В pgAdmin откройте Query Tool на базе `enduro`, загрузите `docs\verify_module1.sql` и нажмите F5.

В выводе должны быть **ожидаемые ошибки** — это и есть доказательство, что база защищает данные:

```
ERROR: conflicting key value violates exclusion constraint "ex_reservation_no_overlap"
ERROR: conflicting key value violates exclusion constraint "ex_maintenance_no_overlap"
ERROR: new row for relation "users" violates check constraint "ck_users_height"
```

Скрипт оставляет тестовые записи. Чтобы вычистить их:

```sql
TRUNCATE motorcycle_reservations, maintenance_periods, booking_participants,
         bookings, motorcycles, payments RESTART IDENTITY CASCADE;
DELETE FROM users WHERE role <> 'owner';
```

---

## Частые проблемы

**`password authentication failed for user "postgres"`**
Пароль в `.env` не совпадает с тем, что задан при установке PostgreSQL. Проверьте, подключившись этим же паролем в pgAdmin.

**`connection refused` на localhost:5432**
Служба PostgreSQL не запущена. `Win + R` → `services.msc` → найдите `postgresql-x64-16` → **Запустить**.

**`relation "users" does not exist`**
Схему накатили не в ту базу — скорее всего в `postgres` вместо `enduro`. Вернитесь к шагу 3 и убедитесь, что в Query Tool выбрана база `enduro` (её имя видно в заголовке вкладки).

**`ModuleNotFoundError: No module named 'aiogram'`**
Не активировано виртуальное окружение. В начале строки PowerShell должно быть `(.venv)`. Если нет — `.\.venv\Scripts\Activate.ps1`.

---

## Про Alembic

Схему вы накатили руками, и скрипт сам проставил отметку версии `0002` в таблице `alembic_version`. Поэтому будущие миграции подхватятся корректно:

```powershell
alembic upgrade head          # применить новые миграции
alembic current               # какая версия сейчас
```

Если вместо pgAdmin захотите накатывать всё через Alembic на чистой базе — просто создайте пустую `enduro` и выполните `alembic upgrade head`, SQL-файл тогда не нужен.

---

## Переход на боевой сервер

Менять код не придётся, только `.env`:

```ini
FSM_STORAGE=redis
STORAGE_BACKEND=s3
S3_ENDPOINT=https://storage.yandexcloud.net
S3_REGION=ru-central1
```

Дальше — `docker compose up -d --build` по инструкции из `README.md`.
