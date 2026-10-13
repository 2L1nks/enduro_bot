"""Расширения PostgreSQL

btree_gist нужен для EXCLUDE-констрейнтов вида
(motorcycle_id WITH =, period WITH &&): обычный GiST не умеет
индексировать bigint по равенству, это даёт как раз btree_gist.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
