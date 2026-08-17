"""Alembic 迁移运行环境。"""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Connection

from alembic import context
from app.accounts._sqlalchemy import _Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = _Base.metadata


def _ensure_postgresql_version_capacity(connection: Connection) -> None:
    """Allow descriptive revision identifiers longer than Alembic's default 32 characters."""
    if connection.dialect.name != "postgresql":
        return
    with connection.begin():
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version ("
                "version_num VARCHAR(255) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                ")"
            )
        )
        connection.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"))


def run_migrations_offline() -> None:
    """在不建立数据库连接时生成迁移 SQL。"""
    context.configure(
        url=os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连接目标数据库并执行迁移。"""
    database_url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _ensure_postgresql_version_capacity(connection)
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
