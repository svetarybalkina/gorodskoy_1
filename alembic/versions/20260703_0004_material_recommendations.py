"""Add extracted material recommendations.

Revision ID: 20260703_0004
Revises: 20260702_0003
Create Date: 2026-07-03
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260703_0004"
down_revision: str | None = "20260702_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "material_recommendations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column(
            "recommendation_type",
            sa.Enum(
                "contact",
                "condition",
                "deadline",
                "restriction",
                "next_step",
                name="recommendation_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("source_fragment", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["materials.id"],
            name=op.f("fk_material_recommendations_material_id_materials"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_recommendations")),
    )
    op.create_index(
        "ix_material_recommendations_material_id",
        "material_recommendations",
        ["material_id"],
        unique=False,
    )
    op.create_index(
        "ix_material_recommendations_type_confidence",
        "material_recommendations",
        ["recommendation_type", "confidence"],
        unique=False,
    )
    op.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS search_index_backup AS
        SELECT material_id, normalized_text, public_text, category_text
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
        SELECT material_id, normalized_text, public_text, category_text, ''
        FROM search_index_backup
        """
    )
    op.execute("DROP TABLE IF EXISTS search_index_backup")


def downgrade() -> None:
    op.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS search_index_backup AS
        SELECT material_id, normalized_text, public_text, category_text
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
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    op.execute(
        """
        INSERT INTO search_index(material_id, normalized_text, public_text, category_text)
        SELECT material_id, normalized_text, public_text, category_text
        FROM search_index_backup
        """
    )
    op.execute("DROP TABLE IF EXISTS search_index_backup")
    op.drop_index("ix_material_recommendations_type_confidence", table_name="material_recommendations")
    op.drop_index("ix_material_recommendations_material_id", table_name="material_recommendations")
    op.drop_table("material_recommendations")
