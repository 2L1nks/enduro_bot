# Бот проката эндуро-мотоциклов

Telegram-бот для проката эндуро и групповых выездов. Клиенты записываются
на выезд через календарь, смотрят свои поездки и фото. Администратор ведёт
парк, календарь записей, оплату, рассылки и статистику.

**Содержание**

1. [Что понадобится в любом случае](#1-что-понадобится-в-любом-случае)
2. [Установка на Windows](#2-установка-на-windows)
3. [Установка на Linux-сервер через Docker](#3-установка-на-linux-сервер-через-docker)
4. [Установка на Linux-сервер без Docker](#4-установка-на-linux-сервер-без-docker)
5. [Настройки .env](#5-настройки-env)
6. [Фото на Яндекс.Диске](#6-фото-на-яндексдиске)
7. [Обновление бота](#7-обновление-бота)
8. [Резервные копии](#8-резервные-копии)
9. [Частые проблемы](#9-частые-проблемы)
10. [Что умеет бот](#10-что-умеет-бот)
11. [Устройство проекта](#11-устройство-проекта)

---

## 1. Что понадобится в любом случае

| Что | Где взять |
|---|---|
| **Токен бота** | Напишите [@BotFather](https://t.me/BotFather) → `/newbot` → придумайте имя и ник. Он пришлёт строку вида `7412345678:AAH...` |
| **Ваш Telegram ID** | Напишите [@userinfobot](https://t.me/userinfobot) — он ответит числом. Этот аккаунт станет владельцем бота |
| **Токен Яндекс.Диска** | Нужен, только если фото клиентов хранятся на Диске — см. [раздел 6](#6-фото-на-яндексдиске) |

Версии: Python **3.11 или 3.12**, PostgreSQL **14 или новее**
(проверено на 16). Одновременно может работать только **один**
экземпляр бота с одним токеном: второй будет отбирать у первого
сообщения, и оба начнут вести себя странно.

---

## 2. Установка на Windows

Подходит для работы с ноутбука или домашнего компьютера: бот работает,
пока включён компьютер и открыто окно с ботом.

### 2.1. Установить Python

1. Скачайте Python 3.12 с [python.org/downloads](https://www.python.org/downloads/windows/).
2. В первом окне установщика **обязательно отметьте «Add python.exe to PATH»**,
   затем «Install Now».
3. Проверка — откройте PowerShell (`Win + X` → «Терминал») и выполните:

   ```powershell
   py --version
   ```

   Должно быть `Python 3.12.x`. Если команда не найдена — переустановите
   Python с галочкой из пункта 2.

### 2.2. Установить PostgreSQL и pgAdmin

1. Скачайте установщик с [postgresql.org/download/windows](https://www.postgresql.org/download/windows/)
   (кнопка «Download the installer», версия 16).
2. При установке оставьте все компоненты отмеченными — pgAdmin и
   модули contrib нужны.
3. Придумайте и **запишите пароль** пользователя `postgres` — он понадобится в `.env`.
4. Порт оставьте `5432`.

### 2.3. Создать базу

1. Откройте pgAdmin, раскройте слева `Servers` → `PostgreSQL 16`
   (спросит пароль из пункта 2.2).
2. Правый клик на **Databases** → **Create** → **Database…**
3. В поле **Database** введите `enduro` → **Save**.

### 2.4. Накатить схему

Выполняется **три файла по порядку**, каждый одинаково:

1. Кликните в дереве на базу **enduro** — именно на неё, не на `postgres`.
2. Меню **Tools** → **Query Tool**.
3. Значок папки (**Open File**) → выберите файл из папки `docs` проекта.
4. Нажмите **F5**.

| По порядку | Файл | Что должно появиться внизу |
|---|---|---|
| 1 | `docs\schema.sql` | Таблиц создано — 13, защита от пересечений — 2, разделов инфо — 7 |
| 2 | `docs\upgrade_0005.sql` | Оплата без клиента разрешена — да |
| 3 | `docs\upgrade_0006.sql` | Аккаунт без телеграма разрешён — да, версия схемы — 0006 |

Все файлы безопасно запускать повторно — если сомневаетесь,
выполнили ли файл, просто выполните его ещё раз.

> Файл `docs\upgrade_0003_0004.sql` при новой установке не нужен:
> всё из него уже есть в `schema.sql`. Он для баз, созданных старыми версиями.

### 2.5. Установить зависимости

Откройте PowerShell **в папке проекта** (в Проводнике: `Shift` + правый клик
по пустому месту папки → «Открыть в Терминале») и выполните:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

В начале строки должно появиться `(.venv)`.

> Если PowerShell пишет, что выполнение сценариев отключено:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
> ответьте `Y` и повторите активацию.

### 2.6. Заполнить настройки

```powershell
copy .env.example .env
notepad .env
```

Обязательно заполнить три строки:

```ini
BOT_TOKEN=токен от @BotFather
OWNER_TG_ID=ваш ID от @userinfobot
POSTGRES_PASSWORD=пароль от PostgreSQL из пункта 2.2
```

Цену, место сбора и остальное — по [разделу 5](#5-настройки-env).
Сохраните файл в кодировке UTF-8 (Блокнот делает так по умолчанию).

### 2.7. Запустить

```powershell
python -m app.main
```

Признак успешного запуска — последняя строка:

```
INFO     [app.main] Бот @ваш_бот готов к работе
```

Предупреждение `FSM: память — незаконченные формы потеряются при перезапуске`
на Windows нормально. Остановить бота — `Ctrl + C`.

Откройте бота в Telegram и отправьте `/start`. Вам как владельцу
доступна команда `/admin`.

### 2.8. Запуск двойным щелчком и автозапуск

В проекте есть `run_windows.bat`: он активирует окружение, запускает бота
и перезапускает его через 10 секунд, если тот упал. Достаточно двойного
щелчка.

Чтобы бот стартовал сам при входе в Windows:

1. `Win + R` → `taskschd.msc` → **Создать простую задачу…**
2. Имя — «Бот проката», триггер — **При входе в Windows**.
3. Действие — **Запустить программу**, программа — полный путь к
   `run_windows.bat`, поле **Рабочая папка** — путь к папке проекта.

Компьютер не должен уходить в сон: «Параметры» → «Система» →
«Питание» → «Переводить в спящий режим» → «Никогда».

---

## 3. Установка на Linux-сервер через Docker

Рекомендуемый вариант для постоянной работы: бот, база и Redis
поднимаются одной командой и сами перезапускаются после сбоев
и перезагрузки сервера. Инструкция для **Ubuntu 22.04 / 24.04**,
на Debian команды те же.

### 3.1. Установить Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Выйдите из SSH и зайдите снова, чтобы группа применилась. Проверка:

```bash
docker compose version
```

### 3.2. Загрузить проект на сервер

С вашего компьютера (PowerShell или терминал), из папки, где лежит архив:

```bash
scp enduro_bot.zip user@адрес_сервера:~
```

На сервере:

```bash
sudo apt install -y unzip
unzip enduro_bot.zip && cd enduro_bot
```

### 3.3. Заполнить настройки

```bash
cp .env.example .env
nano .env
```

Кроме токена и ID задайте **свой пароль базы** и включите Redis:

```ini
BOT_TOKEN=...
OWNER_TG_ID=...
POSTGRES_USER=enduro
POSTGRES_PASSWORD=длинный-случайный-пароль
FSM_STORAGE=redis
STORAGE_BACKEND=yadisk
YANDEX_DISK_TOKEN=...
```

`POSTGRES_HOST` и `REDIS_HOST` трогать не нужно: внутри Docker они
подставляются автоматически. Сохранить в nano — `Ctrl + O`, `Enter`, выйти — `Ctrl + X`.

### 3.4. Запустить

```bash
docker compose up -d --build
docker compose logs -f bot
```

Compose поднимает PostgreSQL и Redis, накатывает схему из `docs/*.sql`
(отдельная разовая задача `migrate`) и запускает бота. Дождитесь строки
`Бот @... готов к работе`, выйти из логов — `Ctrl + C` (бот продолжит работать).

Порт базы наружу не открывается — к ней есть доступ только у бота.

**Если фото хранятся в S3 (MinIO)** вместо Яндекс.Диска, задайте
`STORAGE_BACKEND=s3`, раскомментируйте `aiobotocore` в `requirements.txt`
и запускайте с профилем:

```bash
docker compose --profile s3 up -d --build
```

### 3.5. Полезные команды

```bash
docker compose ps                  # что запущено
docker compose logs -f bot         # логи бота
docker compose restart bot         # перезапуск (например, после правки .env)
docker compose down                # остановить всё (данные сохраняются)
docker compose exec postgres psql -U enduro -d enduro   # консоль базы
```

---

## 4. Установка на Linux-сервер без Docker

Если Docker ставить не хочется. Бот работает как системная служба
и перезапускается сам.

### 4.1. Пакеты

Ubuntu 24.04 (в ней уже Python 3.12):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql unzip
# по желанию — чтобы незаконченные формы переживали перезапуск:
sudo apt install -y redis-server
```

> На Ubuntu 22.04 системный Python — 3.10. Поставьте 3.12:
> `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv`
> и дальше вместо `python3` пишите `python3.12`.

### 4.2. Пользователь и папка

```bash
sudo useradd -r -m -d /opt/enduro_bot -s /usr/sbin/nologin enduro
sudo unzip enduro_bot.zip -d /tmp && sudo cp -r /tmp/enduro_bot/. /opt/enduro_bot/
sudo chown -R enduro:enduro /opt/enduro_bot
```

### 4.3. База

```bash
sudo -u postgres psql -c "CREATE USER enduro WITH PASSWORD 'длинный-случайный-пароль';"
sudo -u postgres psql -c "CREATE DATABASE enduro OWNER enduro;"

cd /opt/enduro_bot
for f in schema.sql upgrade_0005.sql upgrade_0006.sql; do
  PGPASSWORD='длинный-случайный-пароль' psql -h localhost -U enduro -d enduro \
    -v ON_ERROR_STOP=1 -q -f docs/$f || break
done
```

В конце каждый файл печатает табличку проверки — как в
[пункте 2.4](#24-накатить-схему). Сообщения `NOTICE ... already exists, skipping`
при повторном запуске — норма.

### 4.4. Окружение и настройки

```bash
cd /opt/enduro_bot
sudo -u enduro python3 -m venv .venv
sudo -u enduro .venv/bin/pip install -r requirements.txt
sudo -u enduro cp .env.example .env
sudo nano .env
```

В `.env`:

```ini
BOT_TOKEN=...
OWNER_TG_ID=...
POSTGRES_HOST=localhost
POSTGRES_USER=enduro
POSTGRES_PASSWORD=длинный-случайный-пароль
FSM_STORAGE=redis          # или memory, если Redis не ставили
STORAGE_BACKEND=yadisk
YANDEX_DISK_TOKEN=...
```

Закройте файл от чужих глаз — в нём токены и пароли:

```bash
sudo chmod 600 .env && sudo chown enduro:enduro .env
```

Пробный запуск — дождитесь «готов к работе» и остановите `Ctrl + C`:

```bash
sudo -u enduro .venv/bin/python -m app.main
```

### 4.5. Служба systemd

Создайте файл `/etc/systemd/system/enduro-bot.service`:

```ini
[Unit]
Description=Telegram-бот проката эндуро
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=enduro
WorkingDirectory=/opt/enduro_bot
ExecStart=/opt/enduro_bot/.venv/bin/python -m app.main
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now enduro-bot
sudo systemctl status enduro-bot        # должно быть active (running)
sudo journalctl -u enduro-bot -f        # логи
```

---

## 5. Настройки .env

Файл `.env` лежит в корне проекта. Пример со всеми строками — `.env.example`.
После любой правки бота нужно перезапустить: настройки читаются один раз при старте.

| Переменная | Что это | Пример |
|---|---|---|
| `BOT_TOKEN` | Токен от @BotFather | `7412345678:AAH...` |
| `OWNER_TG_ID` | Telegram ID владельца — он получает полный доступ | `123456789` |
| `POSTGRES_HOST` | Адрес базы. В Docker подставляется сам | `localhost` |
| `POSTGRES_PORT` | Порт базы | `5432` |
| `POSTGRES_DB` | Имя базы | `enduro` |
| `POSTGRES_USER` | Пользователь базы. На Windows обычно `postgres` | `enduro` |
| `POSTGRES_PASSWORD` | Пароль базы | — |
| `FSM_STORAGE` | Где хранить незаконченные формы: `memory` (теряются при перезапуске) или `redis` | `redis` |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` | Адрес Redis, если `FSM_STORAGE=redis` | `localhost`, `6379`, `0` |
| `STORAGE_BACKEND` | Где хранить фото: `yadisk`, `local` (папка `storage` рядом с ботом) или `s3` | `yadisk` |
| `YANDEX_DISK_TOKEN` | Токен Яндекс.Диска, см. [раздел 6](#6-фото-на-яндексдиске) | — |
| `YANDEX_DISK_ROOT` | Папка на Диске, внутри неё — папки клиентов | `enduro` |
| `LOCAL_STORAGE_PATH` | Папка для фото при `local` | `storage` |
| `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Только для `s3` | — |
| `PRICE_PER_PERSON` | **Цена выезда за одного человека**, рублей | `5000` |
| `MEETING_POINT` | Место сбора — попадает в подтверждения и напоминания | `Пушкино, база «Эндуро»` |
| `RENTAL_DURATIONS` | Кнопки длительности, минуты через запятую | `60,90,120,180` |
| `SLOT_STEP_MINUTES` | Шаг времени в календаре записи, минуты | `30` |
| `WORKDAY_START`, `WORKDAY_END` | Рабочие часы проката | `09:00`, `20:00` |
| `BOOKING_BUFFER_MINUTES` | Перерыв между выездами на осмотр техники | `30` |
| `MAX_GROUP_SIZE` | Максимум человек в одной группе | `8` |
| `TIMEZONE` | Часовой пояс проката | `Europe/Moscow` |
| `LOG_LEVEL` | Подробность логов: `INFO` или `DEBUG` | `INFO` |

---

## 6. Фото на Яндекс.Диске

У каждого клиента своя папка со случайным именем из 32 символов: ссылку
нельзя подобрать, и по ней не видно чужих фото.

1. Войдите в Яндекс тем аккаунтом, на Диске которого будут храниться фото.
2. Откройте [Полигон Яндекс.Диска](https://yandex.ru/dev/disk/poligon/) и
   нажмите **«Получить OAuth-токен»**, разрешите доступ.
3. Скопируйте токен (начинается с `y0_`) в `.env`:

   ```ini
   STORAGE_BACKEND=yadisk
   YANDEX_DISK_TOKEN=y0_...
   ```

4. Перезапустите бота. В логе при старте должно быть
   `Яндекс.Диск доступен, папка enduro`. Если написано «недоступен» —
   токен неверный или истёк: получите новый.

---

## 7. Обновление бота

1. **Остановите бота** (Windows — `Ctrl + C`; Docker — `docker compose down`;
   systemd — `sudo systemctl stop enduro-bot`).
2. **Сделайте резервную копию базы** ([раздел 8](#8-резервные-копии)).
3. **Замените файлы проекта** новой версией. Свой `.env` и папку `storage`
   не трогайте. Файлы, которых нет в новой версии, удалите — распаковка
   поверх старые файлы не удаляет.
4. **Обновите зависимости**, если менялся `requirements.txt`:
   `pip install -r requirements.txt` (в Docker это делает `--build`).
5. **Накатите новые файлы схемы.** Docker делает это сам при
   `docker compose up -d --build`. На Windows и без Docker выполните
   в pgAdmin или `psql` новые файлы `docs/upgrade_*.sql` по порядку номеров.
6. **Запустите бота.**

Забыть файл схемы не получится: при запуске бот сверяет базу с тем,
что нужно коду. Если чего-то не хватает, он не стартует, а пишет в лог,
какой файл выполнить:

```
ERROR    База не обновлена до текущей версии бота.
Выполните в pgAdmin (Query Tool), по порядку:
  • docs/upgrade_0006.sql — аккаунты людей без телеграма, опознание по ФИО
```

| Файл | Что добавляет |
|---|---|
| `docs/upgrade_0003_0004.sql` | участники без аккаунта, телефон в записи, папки на Яндекс.Диске |
| `docs/upgrade_0005.sql` | оплата за место, а не за человека |
| `docs/upgrade_0006.sql` | аккаунт человека без телеграма по ФИО |

---

## 8. Резервные копии

В базе — все клиенты, записи, оплаты и история. Фото на Яндекс.Диске
хранятся отдельно и в копию базы не входят.

**Windows (pgAdmin):** правый клик на базе `enduro` → **Backup…** →
имя файла, формат **Custom** → **Backup**. Восстановление — правый клик
на пустой базе → **Restore…**

**Docker:**

```bash
docker compose exec -T postgres pg_dump -U enduro -Fc enduro > backup_$(date +%F).dump
# восстановление в пустую базу:
docker compose exec -T postgres pg_restore -U enduro -d enduro --clean < backup_2026-10-14.dump
```

**Linux без Docker** — ежедневная копия в 4 утра, хранится 14 дней:

```bash
sudo mkdir -p /var/backups/enduro && sudo chown postgres /var/backups/enduro
sudo crontab -u postgres -e
```

Добавьте строку:

```
0 4 * * * pg_dump -Fc enduro > /var/backups/enduro/enduro_$(date +\%F).dump && find /var/backups/enduro -name '*.dump' -mtime +14 -delete
```

---

## 9. Частые проблемы

**Бот не стартует, в логе «База не обновлена»** — выполните файлы,
которые он перечислил ([раздел 7](#7-обновление-бота)).

**`password authentication failed`** — пароль в `.env` не совпадает с паролем
базы. На Windows проверьте, что этим же паролем входите в pgAdmin.

**`connection refused` на порту 5432** — PostgreSQL не запущен.
Windows: `Win + R` → `services.msc` → `postgresql-x64-16` → «Запустить».
Linux: `sudo systemctl start postgresql`.

**`relation "users" does not exist`** — схему накатили не в ту базу
(обычно в `postgres` вместо `enduro`). Повторите [пункт 2.4](#24-накатить-схему),
проверив имя базы в заголовке вкладки Query Tool.

**`could not open extension control file "btree_gist"`** — при установке
PostgreSQL не поставились модули contrib. Переустановите со всеми
компонентами (Windows) или `sudo apt install postgresql-contrib` (Linux).

**`ModuleNotFoundError: No module named 'aiogram'`** — не активировано
окружение: в начале строки нет `(.venv)`. Выполните `.\.venv\Scripts\Activate.ps1`
(Windows) или используйте `.venv/bin/python` (Linux).

**`ZoneInfoNotFoundError: 'No time zone found with key Europe/Moscow'`** —
на Windows не установлен пакет `tzdata`: `pip install -r requirements.txt`.

**pgAdmin предупреждает о «collation version mismatch»** — PostgreSQL
обновлял системные библиотеки. Для бота не опасно; убрать предупреждение:
`ALTER DATABASE enduro REFRESH COLLATION VERSION;`

**`Conflict: terminated by other getUpdates request`** — запущены два
экземпляра бота с одним токеном (например, на ноутбуке и на сервере).
Оставьте один.

**Кнопка ничего не делает** — в логе будет строка `НЕ ОБРАБОТАНО: кнопка data=...`.
Пришлите её разработчику: по ней видно, какая кнопка и в каком состоянии формы.

---

## 10. Что умеет бот

**Клиент** (`/start`)

- **Запись на выезд:** календарь со свободной техникой → время → сколько
  человек → длительность → участники → телефон → подтверждение.
  Записывается один человек — ответственный за группу. Товарищам
  телеграм не нужен: их можно назвать по ФИО.
- **Бот помнит людей.** Рост, вес, размер обуви и опыт сохраняются,
  и при следующей записи предлагаются кнопкой «Всё так». Тех, кого
  клиент уже привозил, можно выбрать кнопкой. Клиенту подставляется
  только то, что он вводил сам, — чужие данные он не увидит.
- **Мои записи и история**, отмена записи, **мои фото**, раздел **«О прокате»**.

**Администратор** (`/admin`)

- **Записи** — календарь: на каждом дне значок (🕓 ждёт подтверждения,
  🟢 подтверждено, 🏁 прошло, ✖ только отменённые). Нажатие на дату —
  записи дня. Подтверждение, перенос, отмена, завершение, неявка,
  отметка оплаты. Внесение группы — в том числе на выбранный день
  из календаря. Удаление отменённых записей за день или месяц.
- **Люди без телеграма** получают аккаунт по ФИО — бот узнаёт их
  при следующих записях.
- **Парк** — техника, статусы, обслуживание.
- **Клиенты**, **рассылки**, **инфо-раздел**, **фото клиентам**.
- **Статистика** по месяцам и **выгрузка в Excel**.
- **Сотрудники** (только владелец) — назначение администраторов и механиков.

| Роль | Доступ |
|---|---|
| Клиент | свои записи, фото, статистика |
| Механик | только парк |
| Администратор | всё, кроме сотрудников |
| Владелец | всё |

---

## 11. Устройство проекта

```
enduro_bot/
├── app/                    код бота
│   ├── main.py             точка входа: python -m app.main
│   ├── config.py           настройки из .env
│   ├── db/                 модели, подключение, проверка схемы при старте
│   ├── repositories/       запросы к базе
│   ├── services/           бизнес-логика (не зависит от Telegram)
│   ├── handlers/           обработчики: user/, admin/, common/
│   ├── keyboards/          кнопки и календари
│   ├── middlewares/        сессия БД, роли, защита от дребезга, уборка уведомлений
│   ├── states/             шаги форм
│   ├── scheduler/          отправка напоминаний и рассылок
│   └── utils/              экран, форматирование, проверка ввода
├── docs/                   SQL-схема и обновления — то, что выполняется в pgAdmin/psql
├── tools/                  проверки для разработчика, на сервере не нужны
├── alembic/, alembic.ini   миграции для разработчика, при установке не используются
├── Dockerfile, docker-compose.yml   запуск на сервере через Docker
├── run_windows.bat         запуск на Windows с автоперезапуском
├── requirements.txt        зависимости Python
└── .env.example            образец настроек
```

### Ключевые решения

**Двойная бронь невозможна на уровне базы:**
`EXCLUDE USING gist (motorcycle_id WITH =, period WITH &&)`. Если двое
одновременно займут последний мотоцикл, база не даст создать второй
резерв, и бот скажет «слот только что заняли».

**Заявка не занимает технику** — только подтверждённая админом запись.
Иначе брошенные заявки блокировали бы парк.

**Техника не закрепляется за человеком** — резервируется нужное
количество, а кому какой мотоцикл, решают на месте.

**Напоминания хранятся в базе**, а не в памяти: перезапуск бота
ничего не теряет.

**Один экран:** бот не плодит сообщения, а переписывает одно;
уведомления исчезают после следующего нажатия.

### Проверки для разработчика

Запускаются из папки проекта, сервер для них не нужен:

```bash
python tools/check_project.py   # статический анализ: импорты, кнопки, вызовы, имена
python tools/trace_routes.py    # какое нажатие к какому обработчику попадёт
python tools/smoke_test.py      # бизнес-логика на живой базе (нужен .env)
```
