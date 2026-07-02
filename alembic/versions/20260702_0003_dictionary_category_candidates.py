"""Allow dictionary candidates for new categories.

Revision ID: 20260702_0003
Revises: 20260702_0002
Create Date: 2026-07-02
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260702_0003"
down_revision: str | None = "20260702_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _candidate_type_enum(*values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name="dictionary_candidate_type",
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    with op.batch_alter_table("dictionary_candidates") as batch_op:
        batch_op.alter_column(
            "candidate_type",
            existing_type=_candidate_type_enum("marker", "synonym", "question_variant"),
            type_=_candidate_type_enum("marker", "synonym", "question_variant", "category"),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute("UPDATE dictionary_candidates SET candidate_type = 'marker' WHERE candidate_type = 'category'")
    with op.batch_alter_table("dictionary_candidates") as batch_op:
        batch_op.alter_column(
            "candidate_type",
            existing_type=_candidate_type_enum("marker", "synonym", "question_variant", "category"),
            type_=_candidate_type_enum("marker", "synonym", "question_variant"),
            existing_nullable=False,
        )
