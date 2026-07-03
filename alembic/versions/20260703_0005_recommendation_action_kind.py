"""Add recommendation action kind.

Revision ID: 20260703_0005
Revises: 20260703_0004
Create Date: 2026-07-03 18:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260703_0005"
down_revision = "20260703_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_recommendations",
        sa.Column(
            "action_kind",
            sa.String(length=80),
            nullable=False,
            server_default="general_action",
        ),
    )
    op.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS search_index_backup AS
        SELECT material_id, normalized_text, public_text, category_text, recommendation_text
        FROM search_index
        """
    )
    op.execute("DROP TABLE search_index")
    op.execute(
        """
        CREATE VIRTUAL TABLE search_index USING fts5(
            material_id UNINDEXED,
            normalized_text,
            public_text,
            category_text,
            recommendation_text,
            question_text,
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    op.execute(
        """
        INSERT INTO search_index(material_id, normalized_text, public_text, category_text, recommendation_text, question_text)
        SELECT material_id, normalized_text, public_text, category_text, recommendation_text, ''
        FROM search_index_backup
        """
    )
    op.execute("DROP TABLE IF EXISTS search_index_backup")


def downgrade() -> None:
    op.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS search_index_backup AS
        SELECT material_id, normalized_text, public_text, category_text, recommendation_text
        FROM search_index
        """
    )
    op.execute("DROP TABLE search_index")
    op.execute(
        """
        CREATE VIRTUAL TABLE search_index USING fts5(
            material_id UNINDEXED,
            normalized_text,
            public_text,
            category_text,
            recommendation_text,
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    op.execute(
        """
        INSERT INTO search_index(material_id, normalized_text, public_text, category_text, recommendation_text)
        SELECT material_id, normalized_text, public_text, category_text, recommendation_text
        FROM search_index_backup
        """
    )
    op.execute("DROP TABLE IF EXISTS search_index_backup")
    op.drop_column("material_recommendations", "action_kind")
