"""Run frozen revisions only against the caller's explicitly supplied connection."""

from alembic import context


def run_migrations():
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError(
            "An explicit connection is required; use python -m migration_tools upgrade"
        )
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
