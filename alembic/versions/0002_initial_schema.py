"""Начальная схема

Схема описана явным SQL, а не op.create_table: ключевые вещи здесь —
tstzrange и EXCLUDE USING gist, которые в Alembic-овских хелперах
выглядят гораздо менее читаемо, чем сам DDL. Последующие миграции
можно спокойно делать через autogenerate: он сравнивает с состоянием
базы, а не с историей.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = """
-- ============ ТИПЫ ============
CREATE TYPE user_role AS ENUM ('user', 'mechanic', 'admin', 'owner');
CREATE TYPE experience_level AS ENUM ('beginner', 'intermediate', 'advanced');
CREATE TYPE motorcycle_status AS ENUM ('available', 'maintenance', 'repair', 'retired');
CREATE TYPE booking_status AS ENUM ('pending', 'confirmed', 'cancelled', 'completed', 'no_show');
CREATE TYPE payment_status AS ENUM ('unpaid', 'paid', 'refunded');
CREATE TYPE notification_kind AS ENUM (
    'reminder_24h', 'reminder_2h', 'booking_confirmed',
    'booking_cancelled', 'booking_moved', 'manual'
);
CREATE TYPE notification_status AS ENUM ('scheduled', 'sent', 'failed', 'cancelled');

-- ============ ПОЛЬЗОВАТЕЛИ ============
CREATE TABLE users (
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
    is_blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    added_by_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_users_height CHECK (height_cm IS NULL OR (height_cm BETWEEN 120 AND 230)),
    CONSTRAINT ck_users_weight CHECK (weight_kg IS NULL OR (weight_kg BETWEEN 30 AND 200)),
    CONSTRAINT ck_users_shoe   CHECK (shoe_size IS NULL OR (shoe_size BETWEEN 33 AND 50))
);
CREATE INDEX ix_users_tg_id ON users (tg_id);
CREATE INDEX ix_users_role ON users (role);
CREATE INDEX ix_users_full_name ON users (full_name);

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    action      VARCHAR(64) NOT NULL,
    entity      VARCHAR(64) NOT NULL,
    entity_id   BIGINT,
    details     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_entity ON audit_log (entity, entity_id);

-- ============ ПАРК ============
CREATE TABLE motorcycles (
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
CREATE INDEX ix_moto_status_active ON motorcycles (status, is_active);

-- Плановый простой — интервал, а не глобальный статус:
-- иначе доступность на будущие даты посчитать нельзя.
CREATE TABLE maintenance_periods (
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
CREATE INDEX ix_maintenance_period ON maintenance_periods USING gist (period);

-- ============ ВЫЕЗДЫ ============
CREATE TABLE bookings (
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
CREATE INDEX ix_bookings_starts_at ON bookings (starts_at);
CREATE INDEX ix_bookings_status_starts ON bookings (status, starts_at);

CREATE TABLE booking_participants (
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
CREATE INDEX ix_participants_user ON booking_participants (user_id);
CREATE INDEX ix_participants_leader ON booking_participants (booking_id, is_leader);

-- Ядро доступности. period включает буфер на осмотр и заправку,
-- поэтому любое пересечение интервалов — это конфликт.
-- EXCLUDE делает двойное бронирование физически невозможным:
-- при гонке двух транзакций за последним мотоциклом вторая упадёт.
CREATE TABLE motorcycle_reservations (
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
CREATE INDEX ix_reservation_period ON motorcycle_reservations USING gist (period);
CREATE INDEX ix_reservation_booking ON motorcycle_reservations (booking_id);

CREATE TABLE payments (
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
CREATE INDEX ix_payments_user_status ON payments (user_id, status);

-- ============ ФОТО ============
CREATE TABLE photos (
    id                  BIGSERIAL PRIMARY KEY,
    s3_key              VARCHAR(512) NOT NULL UNIQUE,
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
CREATE INDEX ix_photos_owner_created ON photos (owner_id, created_at);

-- ============ КОНТЕНТ И РАССЫЛКИ ============
CREATE TABLE info_content (
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

CREATE TABLE broadcasts (
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
CREATE TABLE notifications (
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
CREATE INDEX ix_notifications_due ON notifications (status, send_at);
CREATE INDEX ix_notifications_booking_kind ON notifications (booking_id, kind);
"""


DROP = """
DROP TABLE IF EXISTS notifications, broadcasts, info_content, photos, payments,
    motorcycle_reservations, booking_participants, bookings,
    maintenance_periods, motorcycles, audit_log, users CASCADE;
DROP TYPE IF EXISTS notification_status, notification_kind, payment_status,
    booking_status, motorcycle_status, experience_level, user_role;
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute(DROP)
