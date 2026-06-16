"""create mvp schema

Revision ID: 20260616_0001
Revises:
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260616_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("source_file_path", sa.String(length=500), nullable=True),
        sa.Column("anonymized_file_path", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "completed",
                "completed_with_errors",
                "failed",
                name="import_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("total_messages", sa.Integer(), nullable=False),
        sa.Column("processed_messages", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_batches")),
    )
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings")),
        sa.UniqueConstraint("key", name=op.f("uq_settings_key")),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "official_bot",
                "official_channel",
                "website",
                "telegram_bot",
                name="source_kind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("code", name=op.f("uq_sources_code")),
    )
    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topics")),
        sa.UniqueConstraint("slug", name=op.f("uq_topics_slug")),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], name=op.f("fk_categories_topic_id_topics")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("topic_id", "slug", name="uq_categories_topic_id_slug"),
    )
    op.create_index("ix_categories_topic_id_is_public", "categories", ["topic_id", "is_public"], unique=False)
    op.create_table(
        "materials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("import_batch_id", sa.Integer(), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "material_type",
            sa.Enum("official_answer", "official_post", name="material_type", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "active",
                "needs_review",
                "archived",
                "hidden",
                "duplicate",
                "pending_delete",
                name="material_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("public_text", sa.Text(), nullable=False),
        sa.Column("has_personal_data", sa.Boolean(), nullable=False),
        sa.Column("needs_person_name_review", sa.Boolean(), nullable=False),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name=op.f("fk_materials_category_id_categories")),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["materials.id"], name=op.f("fk_materials_duplicate_of_id_materials")),
        sa.ForeignKeyConstraint(["import_batch_id"], ["import_batches.id"], name=op.f("fk_materials_import_batch_id_import_batches")),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], name=op.f("fk_materials_source_id_sources")),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], name=op.f("fk_materials_topic_id_topics")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_materials")),
        sa.UniqueConstraint("source_id", "external_message_id", name="uq_materials_source_id_external_message_id"),
    )
    op.create_index("ix_materials_published_at", "materials", ["published_at"], unique=False)
    op.create_index("ix_materials_source_id", "materials", ["source_id"], unique=False)
    op.create_index("ix_materials_status_topic_category", "materials", ["status", "topic_id", "category_id"], unique=False)
    op.create_table(
        "import_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_batch_id", sa.Integer(), nullable=False),
        sa.Column("report_file_path", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["import_batch_id"], ["import_batches.id"], name=op.f("fk_import_reports_import_batch_id_import_batches")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_reports")),
    )
    op.create_index("ix_import_reports_import_batch_id", "import_reports", ["import_batch_id"], unique=False)
    op.create_table(
        "resident_questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_batch_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("anonymized_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("source_channel", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name=op.f("fk_resident_questions_category_id_categories")),
        sa.ForeignKeyConstraint(["import_batch_id"], ["import_batches.id"], name=op.f("fk_resident_questions_import_batch_id_import_batches")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resident_questions")),
    )
    op.create_index("ix_resident_questions_category_id", "resident_questions", ["category_id"], unique=False)
    op.create_index("ix_resident_questions_import_batch_id", "resident_questions", ["import_batch_id"], unique=False)
    op.create_table(
        "admin_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], name=op.f("fk_admin_notes_material_id_materials")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_notes")),
    )
    op.create_index("ix_admin_notes_material_id", "admin_notes", ["material_id"], unique=False)
    op.create_table(
        "material_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum("imported_pair", "admin_confirmed", "similar", name="link_reason", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], name=op.f("fk_material_links_material_id_materials")),
        sa.ForeignKeyConstraint(["question_id"], ["resident_questions.id"], name=op.f("fk_material_links_question_id_resident_questions")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_links")),
        sa.UniqueConstraint("question_id", "material_id", name="uq_material_links_question_id_material_id"),
    )
    op.create_index("ix_material_links_material_id", "material_links", ["material_id"], unique=False)
    op.create_table(
        "person_name_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("detected_name", sa.String(length=255), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "approved_public", "redacted", "hide_material", name="review_status", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], name=op.f("fk_person_name_reviews_material_id_materials")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person_name_reviews")),
    )
    op.create_index("ix_person_name_reviews_status", "person_name_reviews", ["status"], unique=False)
    op.create_table(
        "problem_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("anonymized_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("shown_material_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("similar_material_ids", sa.JSON(), nullable=False),
        sa.Column(
            "user_action",
            sa.Enum("rephrase", "choose_category", "view_similar", "no_action", name="problem_query_action", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("website", "telegram_bot", name="problem_query_channel", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("match_level", sa.String(length=120), nullable=True),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.Column("is_resolved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name=op.f("fk_problem_queries_category_id_categories")),
        sa.ForeignKeyConstraint(["shown_material_id"], ["materials.id"], name=op.f("fk_problem_queries_shown_material_id_materials")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_problem_queries")),
    )
    op.create_index("ix_problem_queries_category_id", "problem_queries", ["category_id"], unique=False)
    op.create_index("ix_problem_queries_shown_material_id", "problem_queries", ["shown_material_id"], unique=False)
    op.create_table(
        "question_variants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_from_problem_query_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["created_from_problem_query_id"], ["problem_queries.id"], name=op.f("fk_question_variants_created_from_problem_query_id_problem_queries")),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], name=op.f("fk_question_variants_material_id_materials")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_variants")),
    )
    op.create_index("ix_question_variants_material_id_is_confirmed", "question_variants", ["material_id", "is_confirmed"], unique=False)
    op.create_table(
        "redaction_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("redaction_type", sa.String(length=120), nullable=False),
        sa.Column("original_fragment", sa.Text(), nullable=True),
        sa.Column("replacement", sa.String(length=255), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], name=op.f("fk_redaction_events_material_id_materials")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_redaction_events")),
    )
    op.create_index("ix_redaction_events_material_id", "redaction_events", ["material_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_redaction_events_material_id", table_name="redaction_events")
    op.drop_table("redaction_events")
    op.drop_index("ix_question_variants_material_id_is_confirmed", table_name="question_variants")
    op.drop_table("question_variants")
    op.drop_index("ix_problem_queries_shown_material_id", table_name="problem_queries")
    op.drop_index("ix_problem_queries_category_id", table_name="problem_queries")
    op.drop_table("problem_queries")
    op.drop_index("ix_person_name_reviews_status", table_name="person_name_reviews")
    op.drop_table("person_name_reviews")
    op.drop_index("ix_material_links_material_id", table_name="material_links")
    op.drop_table("material_links")
    op.drop_index("ix_admin_notes_material_id", table_name="admin_notes")
    op.drop_table("admin_notes")
    op.drop_index("ix_resident_questions_import_batch_id", table_name="resident_questions")
    op.drop_index("ix_resident_questions_category_id", table_name="resident_questions")
    op.drop_table("resident_questions")
    op.drop_index("ix_import_reports_import_batch_id", table_name="import_reports")
    op.drop_table("import_reports")
    op.drop_index("ix_materials_status_topic_category", table_name="materials")
    op.drop_index("ix_materials_source_id", table_name="materials")
    op.drop_index("ix_materials_published_at", table_name="materials")
    op.drop_table("materials")
    op.drop_index("ix_categories_topic_id_is_public", table_name="categories")
    op.drop_table("categories")
    op.drop_table("topics")
    op.drop_table("sources")
    op.drop_table("settings")
    op.drop_table("import_batches")
