"""add status to users

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-09

0001 baseline 的 users 建表不含 status;本迁移为历史库补列与索引,并保持幂等。
downgrade 显式删列:status 是业务语义字段,回退后代码会退回「无状态」版本。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names() -> set[str]:
    """读取 users 当前列名,让 upgrade 可重复执行。"""
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]) for column in inspector.get_columns("users")}


def _index_names() -> set[str]:
    """读取 users 当前索引名,避免重复建索引。"""
    inspector = sa.inspect(op.get_bind())
    return {str(index["name"]) for index in inspector.get_indexes("users")}


def upgrade() -> None:
    """补出 status 列(默认 active)与索引;已存在的部分逐个跳过。"""
    if "status" not in _column_names():
        op.add_column(
            "users",
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
        )
    if "ix_users_status" not in _index_names():
        op.create_index("ix_users_status", "users", ["status"])


def downgrade() -> None:
    """回退到 0002:先删索引再删列。"""
    if "ix_users_status" in _index_names():
        op.drop_index("ix_users_status", table_name="users")
    if "status" in _column_names():
        op.drop_column("users", "status")
