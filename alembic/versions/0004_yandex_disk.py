"""Папка клиента на Яндекс.Диске

Фото каждого клиента лежат в отдельной папке со случайным именем.
Имя хранится у пользователя, публичная ссылка — рядом, чтобы
не дёргать API Диска на каждый показ.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS disk_folder     VARCHAR(64),
            ADD COLUMN IF NOT EXISTS disk_public_url VARCHAR(512);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_disk_folder
            ON users (disk_folder) WHERE disk_folder IS NOT NULL;

        ALTER TABLE photos
            ADD COLUMN IF NOT EXISTS disk_path VARCHAR(512);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS uq_users_disk_folder;
        ALTER TABLE users
            DROP COLUMN IF EXISTS disk_public_url,
            DROP COLUMN IF EXISTS disk_folder;
        ALTER TABLE photos DROP COLUMN IF EXISTS disk_path;
        """
    )
