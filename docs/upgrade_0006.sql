-- Обновление схемы до версии 0006
--
-- Что меняется: у человека без телеграма появляется свой аккаунт,
-- который опознаётся по ФИО. В нём хранятся рост, вес, обувь и опыт —
-- при следующей записи они подставляются сами.
--
-- Как запускать: открыть в pgAdmin на базе бота и выполнить целиком.
-- Скрипт безопасно запускать повторно. Сначала должен быть выполнен
-- docs/upgrade_0005.sql.

BEGIN;

ALTER TABLE users ALTER COLUMN tg_id DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS name_key VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_offline_name
    ON users (name_key) WHERE tg_id IS NULL;

ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_identity;
ALTER TABLE users ADD CONSTRAINT ck_users_identity
    CHECK (tg_id IS NOT NULL OR name_key IS NOT NULL);

UPDATE alembic_version SET version_num = '0006';

COMMIT;

-- ---------- Проверка ----------
SELECT 'Аккаунт без телеграма разрешён' AS проверка,
       CASE WHEN is_nullable = 'YES' THEN 'да' ELSE 'НЕТ' END AS результат
FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'tg_id'
UNION ALL
SELECT 'ФИО уникально у людей без телеграма',
       CASE WHEN count(*) > 0 THEN 'да' ELSE 'НЕТ' END
FROM pg_indexes WHERE indexname = 'uq_users_offline_name'
UNION ALL
SELECT 'Версия схемы', version_num FROM alembic_version;
