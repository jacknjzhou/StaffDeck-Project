"""add team_id to channel_bindings

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08

0001 baseline 已包含 channel_bindings.team_id,全新库升级到 0002 时必须跳过,
否则 add_column 会因列已存在而报错;本迁移只为从 0001 之前的历史库补齐该列。
downgrade 是显式的 no-op:该列归 0001 所有,回退时删掉会让 0001 库结构不完整。
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
    """在历史库上补出 team_id 列及其索引;已存在的部分逐个跳过。"""
    if "team_id" not in _column_names():
        op.add_column("channel_bindings", sa.Column("team_id", sa.String(), nullable=True))
    if "ix_channel_bindings_team_id" not in _index_names():
        op.create_index("ix_channel_bindings_team_id", "channel_bindings", ["team_id"])


def downgrade() -> None:
    """不做事:team_id 列与索引归 0001 baseline 所有。

    0001 的 create_table 已声明该列并建出 ix_channel_bindings_team_id,所以回退到 0001 时
    删列会让「stamped 0001 的库」缺少 0001 自己定义的结构,后续任何读到 team_id 的代码都会失败。
    多一列(历史库回退后)是良性的,少一列是致命的,因此这里保留列与索引。
    """
    return
