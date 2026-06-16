from __future__ import annotations

import logging

from alembic import command
from alembic.config import Config

from app.core.config import Settings
from app.db.seed import seed_initial_data
from app.db.session import SessionLocal


logger = logging.getLogger(__name__)


def bootstrap_database(settings: Settings) -> None:
    """Apply migrations and seed stable reference data for local one-command startup."""
    logger.info("Applying database migrations before application startup.")
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_config, "head")

    logger.info("Seeding initial public taxonomy and settings.")
    with SessionLocal() as session:
        seed_initial_data(session)
