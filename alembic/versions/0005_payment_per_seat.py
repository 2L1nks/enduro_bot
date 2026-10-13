"""Оплата за место, а не за человека

Деньги берут с каждого в группе, но платит обычно один. У спутников
может не быть телеграма, а бывает, что аккаунта нет ни у кого — группу
завёл админ с улицы. Чтобы выручка не терялась, платёж перестал
требовать клиента.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE payments ALTER COLUMN user_id DROP NOT NULL;
        """
    )


def downgrade() -> None:
    # Обезличенные платежи вернуть владельцу невозможно — удаляем,
    # иначе NOT NULL не наложится
    op.execute(
        """
        DELETE FROM payments WHERE user_id IS NULL;
        ALTER TABLE payments ALTER COLUMN user_id SET NOT NULL;
        """
    )
