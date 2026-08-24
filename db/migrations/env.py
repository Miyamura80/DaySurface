"""Alembic environment configuration."""

from alembic import context
from sqlalchemy import create_engine

# Import all models so Base.metadata knows about them
import db.models.api_keys  # noqa: F401
import db.models.gmail_push  # noqa: F401
import db.models.google_tokens  # noqa: F401
import db.models.idempotency_keys  # noqa: F401
import db.models.payment_settlements  # noqa: F401
import db.models.pdf_documents  # noqa: F401
import db.models.processed_stripe_events  # noqa: F401
import db.models.profiles  # noqa: F401
import db.models.thread_curation  # noqa: F401
import db.models.user_subscriptions  # noqa: F401
import db.models.waitlist_signups  # noqa: F401
import db.models.webhooks  # noqa: F401
from common import global_config
from db.base import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live connection)."""
    url = global_config.BACKEND_DB_URI
    if not url:
        raise RuntimeError("BACKEND_DB_URI must be set for migrations")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live DB connection)."""
    url = global_config.BACKEND_DB_URI
    if not url:
        raise RuntimeError("BACKEND_DB_URI must be set for migrations")
    connectable = create_engine(url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
