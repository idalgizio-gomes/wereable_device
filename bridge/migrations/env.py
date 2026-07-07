import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# storage_advanced.py vive em bridge/, um nível acima de bridge/migrations/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import storage_advanced  # noqa: E402  (import depois do sys.path.insert, de propósito)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `storage_advanced.py` já lê DATABASE_URL do ambiente (omissão:
# sqlite:///./carewear.db, ver "CONFIGURAÇÃO" nesse ficheiro) — reutilizamos
# a mesma variável aqui em vez de duplicar a URL em alembic.ini, para as
# duas nunca poderem divergir.
db_url = os.environ.get("DATABASE_URL", "sqlite:///./carewear.db")
config.set_main_option("sqlalchemy.url", db_url)

# Alvo do autogenerate: o mesmo Base.metadata usado pela aplicação.
target_metadata = storage_advanced.Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite não suporta ALTER TABLE para a maioria das operações
        # (renomear/remover coluna, mudar tipo) — o modo "batch" do Alembic
        # contorna isto recriando a tabela. Sem efeito prático em PostgreSQL
        # (produção), onde ALTER TABLE nativo já funciona.
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
