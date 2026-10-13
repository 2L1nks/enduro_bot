"""Группа с ответственным: участники без аккаунта

Записывается один человек — он главный по группе. Телеграм-аккаунты
остальных необязательны, поэтому user_id становится необязательным,
а имя участника хранится прямо в записи.

Заодно убираем запрет на повторное участие: раньше он защищал от
дублей, но теперь в одной записи может быть несколько участников
без аккаунта, и для них ограничение по user_id бессмысленно.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE = """
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
"""

DOWNGRADE = """
DROP INDEX IF EXISTS ix_participants_leader;
ALTER TABLE bookings DROP COLUMN IF EXISTS contact_phone;
ALTER TABLE booking_participants
    DROP COLUMN IF EXISTS is_leader,
    DROP COLUMN IF EXISTS tg_username,
    DROP COLUMN IF EXISTS full_name;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
