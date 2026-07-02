"""Add search FTS index and dictionary candidates.

Revision ID: 20260702_0002
Revises: 20260616_0001
Create Date: 2026-07-02
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260702_0002"
down_revision: str | None = "20260616_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dictionary_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column(
            "candidate_type",
            sa.Enum(
                "marker",
                "synonym",
                "question_variant",
                name="dictionary_candidate_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "search",
                "import",
                name="dictionary_candidate_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                name="dictionary_candidate_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column("problem_query_id", sa.Integer(), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name=op.f("fk_dictionary_candidates_category_id_categories")),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], name=op.f("fk_dictionary_candidates_material_id_materials")),
        sa.ForeignKeyConstraint(["problem_query_id"], ["problem_queries.id"], name=op.f("fk_dictionary_candidates_problem_query_id_problem_queries")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dictionary_candidates")),
        sa.UniqueConstraint(
            "normalized_text",
            "candidate_type",
            "category_id",
            "material_id",
            name="uq_dictionary_candidates_normalized_type_category_material",
        ),
    )
    op.create_index("ix_dictionary_candidates_category_id", "dictionary_candidates", ["category_id"], unique=False)
    op.create_index("ix_dictionary_candidates_material_id", "dictionary_candidates", ["material_id"], unique=False)
    op.create_index("ix_dictionary_candidates_status", "dictionary_candidates", ["status"], unique=False)
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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search_index")
    op.drop_index("ix_dictionary_candidates_status", table_name="dictionary_candidates")
    op.drop_index("ix_dictionary_candidates_material_id", table_name="dictionary_candidates")
    op.drop_index("ix_dictionary_candidates_category_id", table_name="dictionary_candidates")
    op.drop_table("dictionary_candidates")
