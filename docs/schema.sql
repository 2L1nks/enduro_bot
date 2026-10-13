-- ============================================================
--  Бот проката эндуро — начальная схема БД
--
--  Запуск: pgAdmin -> правый клик на базе enduro -> Query Tool ->
--          открыть этот файл (папка с ленточкой) -> F5
--
--  ВАЖНО: подключитесь именно к базе enduro, а не к служебной
--         базе postgres. Скрипт можно запускать повторно -
--         он не ломает уже созданное.
--
--  После него выполните по порядку docs/upgrade_0005.sql и
--  docs/upgrade_0006.sql (схема здесь соответствует версии 0004).
-- ============================================================

-- btree_gist учит GiST-индекс сравнивать bigint по равенству.
-- Без него EXCLUDE (motorcycle_id WITH =, period WITH &&) не создастся.
-- Расширение входит в стандартный установщик PostgreSQL для Windows.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ============ ТИПЫ ============
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('user', 'mechanic', 'admin', 'owner');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE experience_level AS ENUM ('beginner', 'intermediate', 'advanced');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE motorcycle_status AS ENUM ('available', 'maintenance', 'repair', 'retired');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE booking_status AS ENUM ('pending', 'confirmed', 'cancelled', 'completed', 'no_show');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE payment_status AS ENUM ('unpaid', 'paid', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE notification_kind AS ENUM (
        'reminder_24h', 'reminder_2h', 'booking_confirmed',
        'booking_cancelled', 'booking_moved', 'manual'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE TYPE notification_status AS ENUM ('scheduled', 'sent', 'failed', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============ ПОЛЬЗОВАТЕЛИ ============
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    tg_id           BIGINT NOT NULL UNIQUE,
    username        VARCHAR(64),
    full_name       VARCHAR(255) NOT NULL DEFAULT '',
    phone           VARCHAR(32),
    role            user_role NOT NULL DEFAULT 'user',
    height_cm       INTEGER,
    weight_kg       INTEGER,
    shoe_size       INTEGER,
    experience      experience_level,
    notes           TEXT,
    disk_folder     VARCHAR(64),
    disk_public_url VARCHAR(512),
    is_blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    added_by_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_users_height CHECK (height_cm IS NULL OR (height_cm BETWEEN 120 AND 230)),
    CONSTRAINT ck_users_weight CHECK (weight_kg IS NULL OR (weight_kg BETWEEN 30 AND 200)),
    CONSTRAINT ck_users_shoe   CHECK (shoe_size IS NULL OR (shoe_size BETWEEN 33 AND 50))
);
CREATE INDEX IF NOT EXISTS ix_users_tg_id ON users (tg_id);
CREATE INDEX IF NOT EXISTS ix_users_role ON users (role);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_disk_folder ON users (disk_folder) WHERE disk_folder IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_users_full_name ON users (full_name);

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    action      VARCHAR(64) NOT NULL,
    entity      VARCHAR(64) NOT NULL,
    entity_id   BIGINT,
    details     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_log (entity, entity_id);

-- ============ ПАРК ============
CREATE TABLE IF NOT EXISTS motorcycles (
    id                  BIGSERIAL PRIMARY KEY,
    title               VARCHAR(128) NOT NULL,
    model               VARCHAR(128) NOT NULL DEFAULT '',
    reg_number          VARCHAR(32) UNIQUE,
    engine_cc           INTEGER,
    category            experience_level NOT NULL DEFAULT 'beginner',
    seat_height_mm      INTEGER,
    min_rider_height_cm INTEGER,
    max_rider_height_cm INTEGER,
    max_rider_weight_kg INTEGER,
    status              motorcycle_status NOT NULL DEFAULT 'available',
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    photo_s3_key        VARCHAR(512),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_moto_height_range CHECK (
        min_rider_height_cm IS NULL OR max_rider_height_cm IS NULL
        OR min_rider_height_cm <= max_rider_height_cm
    )
);
CREATE INDEX IF NOT EXISTS ix_moto_status_active ON motorcycles (status, is_active);

-- Плановый простой — интервал, а не глобальный статус:
-- иначе доступность на будущие даты посчитать нельзя.
CREATE TABLE IF NOT EXISTS maintenance_periods (
    id              BIGSERIAL PRIMARY KEY,
    motorcycle_id   BIGINT NOT NULL REFERENCES motorcycles(id) ON DELETE CASCADE,
    period          TSTZRANGE NOT NULL,
    reason          VARCHAR(255) NOT NULL DEFAULT '',
    created_by_id   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ex_maintenance_no_overlap
        EXCLUDE USING gist (motorcycle_id WITH =, period WITH &&)
);
CREATE INDEX IF NOT EXISTS ix_maintenance_period ON maintenance_periods USING gist (period);

-- ============ ВЫЕЗДЫ ============
CREATE TABLE IF NOT EXISTS bookings (
    id              BIGSERIAL PRIMARY KEY,
    created_by_id   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ NOT NULL,
    status          booking_status NOT NULL DEFAULT 'pending',
    requested_seats INTEGER NOT NULL DEFAULT 1,
    meeting_point   VARCHAR(512),
    total_price     NUMERIC(10, 2) NOT NULL DEFAULT 0,
    comment         TEXT,
    cancel_reason   VARCHAR(512),
    contact_phone   VARCHAR(32),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_booking_time_order CHECK (ends_at > starts_at),
    CONSTRAINT ck_booking_seats_positive CHECK (requested_seats > 0),
    CONSTRAINT ck_booking_price_non_negative CHECK (total_price >= 0)
);
CREATE INDEX IF NOT EXISTS ix_bookings_starts_at ON bookings (starts_at);
CREATE INDEX IF NOT EXISTS ix_bookings_status_starts ON bookings (status, starts_at);

CREATE TABLE IF NOT EXISTS booking_participants (
    id              BIGSERIAL PRIMARY KEY,
    booking_id      BIGINT NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    user_id         BIGINT REFERENCES users(id) ON DELETE SET NULL,
    full_name       VARCHAR(255),
    tg_username     VARCHAR(64),
    is_leader       BOOLEAN NOT NULL DEFAULT FALSE,
    motorcycle_id   BIGINT REFERENCES motorcycles(id) ON DELETE SET NULL,
    height_cm       INTEGER,
    weight_kg       INTEGER,
    shoe_size       INTEGER,
    experience      experience_level,
    price           NUMERIC(10, 2) NOT NULL DEFAULT 0,
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_part_height CHECK (height_cm IS NULL OR (height_cm BETWEEN 120 AND 230)),
    CONSTRAINT ck_part_weight CHECK (weight_kg IS NULL OR (weight_kg BETWEEN 30 AND 200)),
    CONSTRAINT ck_part_shoe   CHECK (shoe_size IS NULL OR (shoe_size BETWEEN 33 AND 50)),
    CONSTRAINT ck_part_price_non_negative CHECK (price >= 0)
);
CREATE INDEX IF NOT EXISTS ix_participants_user ON booking_participants (user_id);
CREATE INDEX IF NOT EXISTS ix_participants_leader ON booking_participants (booking_id, is_leader);

-- Ядро доступности. period включает буфер на осмотр и заправку,
-- поэтому любое пересечение интервалов — это конфликт.
-- EXCLUDE делает двойное бронирование физически невозможным:
-- при гонке двух транзакций за последним мотоциклом вторая упадёт.
CREATE TABLE IF NOT EXISTS motorcycle_reservations (
    id              BIGSERIAL PRIMARY KEY,
    motorcycle_id   BIGINT NOT NULL REFERENCES motorcycles(id) ON DELETE RESTRICT,
    booking_id      BIGINT NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    participant_id  BIGINT UNIQUE REFERENCES booking_participants(id) ON DELETE CASCADE,
    period          TSTZRANGE NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ex_reservation_no_overlap
        EXCLUDE USING gist (motorcycle_id WITH =, period WITH &&)
);
CREATE INDEX IF NOT EXISTS ix_reservation_period ON motorcycle_reservations USING gist (period);
CREATE INDEX IF NOT EXISTS ix_reservation_booking ON motorcycle_reservations (booking_id);

CREATE TABLE IF NOT EXISTS payments (
    id              BIGSERIAL PRIMARY KEY,
    booking_id      BIGINT NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    amount          NUMERIC(10, 2) NOT NULL,
    method          VARCHAR(32) NOT NULL DEFAULT 'cash',
    status          payment_status NOT NULL DEFAULT 'unpaid',
    paid_at         TIMESTAMPTZ,
    recorded_by_id  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_payment_amount_non_negative CHECK (amount >= 0)
);
CREATE INDEX IF NOT EXISTS ix_payments_user_status ON payments (user_id, status);

-- ============ ФОТО ============
CREATE TABLE IF NOT EXISTS photos (
    id                  BIGSERIAL PRIMARY KEY,
    s3_key              VARCHAR(512) NOT NULL UNIQUE,
    disk_path           VARCHAR(512),
    telegram_file_id    VARCHAR(256),
    owner_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    booking_id          BIGINT REFERENCES bookings(id) ON DELETE SET NULL,
    uploaded_by_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    mime_type           VARCHAR(64) NOT NULL DEFAULT 'image/jpeg',
    size_bytes          BIGINT,
    caption             VARCHAR(512),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_photos_owner_created ON photos (owner_id, created_at);

-- ============ КОНТЕНТ И РАССЫЛКИ ============
CREATE TABLE IF NOT EXISTS info_content (
    id              BIGSERIAL PRIMARY KEY,
    slug            VARCHAR(64) NOT NULL UNIQUE,
    title           VARCHAR(255) NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    media           JSONB NOT NULL DEFAULT '[]'::jsonb,
    sort_order      INTEGER NOT NULL DEFAULT 100,
    updated_by_id   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id          BIGSERIAL PRIMARY KEY,
    author_id   BIGINT REFERENCES users(id) ON DELETE SET NULL,
    text        TEXT NOT NULL,
    audience    JSONB NOT NULL DEFAULT '{}'::jsonb,
    media       JSONB NOT NULL DEFAULT '[]'::jsonb,
    total       INTEGER NOT NULL DEFAULT 0,
    sent        INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    finished_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Очередь исходящих сообщений живёт в БД, а не в памяти планировщика:
-- рестарт бота не должен терять напоминания.
CREATE TABLE IF NOT EXISTS notifications (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    booking_id      BIGINT REFERENCES bookings(id) ON DELETE CASCADE,
    broadcast_id    BIGINT REFERENCES broadcasts(id) ON DELETE CASCADE,
    kind            notification_kind NOT NULL,
    status          notification_status NOT NULL DEFAULT 'scheduled',
    send_at         TIMESTAMPTZ NOT NULL,
    sent_at         TIMESTAMPTZ,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_notifications_due ON notifications (status, send_at);
CREATE INDEX IF NOT EXISTS ix_notifications_booking_kind ON notifications (booking_id, kind);

-- ============ ЗАГОТОВКИ ИНФО-РАЗДЕЛА ============
-- Бот создаёт их и сам при первом старте; здесь - чтобы разделы
-- были видны в pgAdmin сразу после наката схемы.
INSERT INTO info_content (slug, title, body, sort_order) VALUES
  ('about',    'О прокате',             'Расскажите здесь о компании. Текст редактируется из админки.', 10),
  ('fleet',    'Наши мотоциклы',        'Описание моделей и для кого они подходят.',                    20),
  ('prices',   'Цены',                  'Стоимость аренды и что в неё входит.',                         30),
  ('rules',    'Правила',               'Требования к участникам, залог, ответственность.',             40),
  ('gear',     'Экипировка',            'Что выдаём, что нужно привезти с собой.',                      50),
  ('route',    'Маршрут и место сбора', 'Как добраться до базы.',                                       60),
  ('contacts', 'Контакты',              'Телефон, мессенджеры, соцсети.',                               70)
ON CONFLICT (slug) DO NOTHING;

-- ============ ОТМЕТКА ДЛЯ ALEMBIC ============
-- Схема накачена руками, поэтому фиксируем версию: иначе Alembic
-- при следующей миграции попробует создать всё заново.
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL CONSTRAINT alembic_version_pkc PRIMARY KEY
);
-- Только в пустую таблицу: на уже обновлённой базе версия не откатится
INSERT INTO alembic_version (version_num)
SELECT '0004' WHERE NOT EXISTS (SELECT 1 FROM alembic_version);

-- ============ ПРОВЕРКА ============
SELECT 'Таблиц создано (ожидается 13)' AS check, count(*)::text AS result
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
UNION ALL
SELECT 'Защита от пересечений (ожидается 2)', count(*)::text
FROM pg_constraint WHERE contype = 'x'
UNION ALL
SELECT 'Разделов инфо (ожидается 7)', count(*)::text FROM info_content;
