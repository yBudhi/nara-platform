import os
import re
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_ROLE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _role_identifier(env_var: str, default: str) -> str:
    value = os.getenv(env_var, default)
    if not _ROLE_IDENTIFIER.fullmatch(value):
        raise RuntimeError(f"{env_var} must be a valid PostgreSQL role identifier")
    return value


migrator_role = _role_identifier("ALEMBIC_MIGRATOR_ROLE", "nara_migrator")
owner_role = _role_identifier("ALEMBIC_OWNER_ROLE", "nara_owner")

database_url = os.getenv("ALEMBIC_DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "ALEMBIC_DATABASE_URL must be set to the nara_migrator database URL"
    )
if database_url.startswith("postgresql://"):
    raise RuntimeError(
        "ALEMBIC_DATABASE_URL must use the postgresql+psycopg:// driver"
    )

config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def _set_owner_role(connection: Connection) -> None:
    current_user = connection.execute(text("SELECT current_user")).scalar_one()
    if current_user != migrator_role:
        raise RuntimeError(
            "Alembic must authenticate as "
            f"{migrator_role}, but connected as {current_user}"
        )
    connection.execute(text(f'SET LOCAL ROLE "{owner_role}"'))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.execute(f'SET LOCAL ROLE "{owner_role}"')
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            _set_owner_role(connection)
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
