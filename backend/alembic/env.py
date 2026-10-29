import os
from logging.config import fileConfig

import app.db.models
from alembic import context
from app.db.database import build_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = app.db.models.SQLModel.metadata


def _database_url() -> str:
    """解析迁移目标库地址，优先取 STAFFDECK_DATABASE_URL，否则回落到应用配置。"""
    url = os.environ.get("STAFFDECK_DATABASE_URL", "")
    if url:
        return url
    from app.config import get_settings

    return get_settings().database_url


def run_migrations_online() -> None:
    """在线模式：建立真实连接，在事务中执行全部迁移并允许类型比较。"""
    connectable = build_engine(_database_url())
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline() -> None:
    """离线模式：仅用连接串生成 SQL，不建立真实连接，字面量内联参数。"""
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
