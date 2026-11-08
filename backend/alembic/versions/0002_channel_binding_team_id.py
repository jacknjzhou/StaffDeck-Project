"""add team_id to channel_bindings

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08

0001 baseline 已包含 channel_bindings.team_id,全新库升级到 0002 时必须跳过,
否则 add_column 会因列已存在而报错;本迁移只为从 0001 之前的历史库补齐该列。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names() -> set[str]:
    """读取 channel_bindings 当前列名,用于让 upgrade/downgrade 保持幂等。"""
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]) for column in inspector.get_columns("channel_bindings")}


def _index_names() -> set[str]:
    """读取 channel_bindings 当前索引名,避免 drop_index 打到不存在的索引。"""
    inspector = sa.inspect(op.get_bind())
    return {str(index["name"]) for index in inspector.get_indexes("channel_bindings")}


def upgrade() -> None:
    """在历史库上补出 team_id 列及其索引;列已存在时不做任何事。"""
    if "team_id" in _column_names():
        return
    op.add_column("channel_bindings", sa.Column("team_id", sa.String(), nullable=True))
    op.create_index("ix_channel_bindings_team_id", "channel_bindings", ["team_id"])


def downgrade() -> None:
    """回退 team_id 列及其索引;列不存在时不做任何事。"""
    if "team_id" not in _column_names():
        return
    if "ix_channel_bindings_team_id" in _index_names():
        op.drop_index("ix_channel_bindings_team_id", table_name="channel_bindings")
    op.drop_column("channel_bindings", "team_id")
