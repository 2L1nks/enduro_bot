-- ============================================================
--  Обновление существующей базы: миграции 0003 и 0004
--
--  Запуск: pgAdmin -> база enduro -> Query Tool -> открыть
--          этот файл -> F5. Повторный запуск безопасен.
--
--  Что меняется:
--   0003 — группу записывает один человек (ответственный),
--          телеграм-аккаунты остальных необязательны;
--          у записи появляется телефон ответственного
--   0004 — у клиента появляется личная папка на Яндекс.Диске
--
--  Данные не теряются: имена участников переносятся из профилей,
--  автор каждой записи помечается ответственным за группу.
-- ============================================================

-- ---------- 0003: группа с ответственным ----------
ALTER TABLE booking_participants
    ALTER COLUMN user_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS full_name   VARCHAR(255),
    ADD COLUMN IF NOT EXISTS tg_username VARCHAR(64),
    ADD COLUMN IF NOT EXISTS is_leader   BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE booking_participants
    DROP CONSTRAINT IF EXISTS uq_participant_once_per_booking;

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(32);

-- Имена уже заведённых участников переносим из профилей,
-- чтобы старые записи отображались одинаково с новыми
UPDATE booking_participants p
SET full_name = u.full_name,
    tg_username = u.username
FROM users u
WHERE p.user_id = u.id AND p.full_name IS NULL;

-- Автор каждой записи считается ответственным за группу
UPDATE booking_participants p
SET is_leader = TRUE
FROM bookings b
WHERE p.booking_id = b.id AND p.user_id = b.created_by_id;

CREATE INDEX IF NOT EXISTS ix_participants_leader
    ON booking_participants (booking_id, is_leader);

-- ---------- 0004: папка на Яндекс.Диске ----------
ALTER TABLE users
            ADD COLUMN IF NOT EXISTS disk_folder     VARCHAR(64),
            ADD COLUMN IF NOT EXISTS disk_public_url VARCHAR(512);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_disk_folder
            ON users (disk_folder) WHERE disk_folder IS NOT NULL;

        ALTER TABLE photos
            ADD COLUMN IF NOT EXISTS disk_path VARCHAR(512);

-- ---------- Отметка версии для Alembic ----------
UPDATE alembic_version SET version_num = '0004';

-- ---------- Проверка ----------
SELECT 'Участники без аккаунта разрешены' AS проверка,
       CASE WHEN is_nullable = 'YES' THEN 'да' ELSE 'НЕТ' END AS результат
FROM information_schema.columns
WHERE table_name = 'booking_participants' AND column_name = 'user_id'
UNION ALL
SELECT 'Телефон в записи', CASE WHEN count(*) > 0 THEN 'да' ELSE 'НЕТ' END
FROM information_schema.columns
WHERE table_name = 'bookings' AND column_name = 'contact_phone'
UNION ALL
SELECT 'Папка на Диске у клиента', CASE WHEN count(*) > 0 THEN 'да' ELSE 'НЕТ' END
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'disk_folder'
UNION ALL
SELECT 'Версия схемы', version_num FROM alembic_version;
