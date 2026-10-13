-- Обновление схемы до версии 0005
--
-- Что меняется: оплата больше не требует клиента с телеграм-аккаунтом.
-- Деньги берут за каждое место в группе, а платит обычно один человек —
-- ответственный. Если аккаунта нет ни у кого (группу завёл админ),
-- выручка всё равно должна попасть в статистику.
--
-- Как запускать: открыть в pgAdmin на базе бота и выполнить целиком.
-- Скрипт безопасно запускать повторно.

BEGIN;

ALTER TABLE payments ALTER COLUMN user_id DROP NOT NULL;

UPDATE alembic_version SET version_num = '0005';

COMMIT;

-- ---------- Проверка ----------
SELECT 'Оплата без клиента разрешена' AS проверка,
       CASE WHEN is_nullable = 'YES' THEN 'да' ELSE 'НЕТ' END AS результат
FROM information_schema.columns
WHERE table_name = 'payments' AND column_name = 'user_id'
UNION ALL
SELECT 'Версия схемы', version_num FROM alembic_version;
