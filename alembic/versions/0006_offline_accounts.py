"""Аккаунт человека без телеграма — по уникальному ФИО

Товарищ, которого привозит клиент, часто без телеграма. Раньше он
оставался безымянным «Участником 2», и в следующий раз о нём ничего
не было известно. Теперь у него свой аккаунт: опознаётся по ФИО,
хранит рост, вес, обувь и опыт, и при повторной записи данные
подставляются сами.

name_key — ФИО в нормальной форме (нижний регистр, ё→е, одиночные
пробелы). Уникальность держит база, а не код: два одновременных
внесения одного и того же человека не создадут двойника.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ALTER COLUMN tg_id DROP NOT NULL;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS name_key VARCHAR(255);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_offline_name
            ON users (name_key) WHERE tg_id IS NULL;

        ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_identity;
        ALTER TABLE users ADD CONSTRAINT ck_users_identity
            CHECK (tg_id IS NOT NULL OR name_key IS NOT NULL);
        """
    )


def downgrade() -> None:
    # Аккаунты без телеграма вернуть в старую схему нельзя: участие
    # в выездах отвязывается (SET NULL), сами аккаунты удаляются
    op.execute(
        """
        DELETE FROM users WHERE tg_id IS NULL;
        ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_identity;
        DROP INDEX IF EXISTS uq_users_offline_name;
        ALTER TABLE users DROP COLUMN IF EXISTS name_key;
        ALTER TABLE users ALTER COLUMN tg_id SET NOT NULL;
        """
    )
