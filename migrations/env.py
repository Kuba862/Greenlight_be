from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from backend.config import get_settings
from backend.database import database_url
from backend.models import Base


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

migration_url = database_url.set(
    port=get_settings().db_migration_port,
)


def include_name(name, type_, parent_names):
    if type_ == "table":
        return name.startswith("mpk_")

    return True


def run_migrations_offline():
    context.configure(
        url=migration_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="mpk_alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(
        migration_url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "sslmode": "require",
            "connect_timeout": 10,
            "prepare_threshold": None,
        },
    )

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_name=include_name,
                include_schemas=False,
                version_table="mpk_alembic_version",
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()