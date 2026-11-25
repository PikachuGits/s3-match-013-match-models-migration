"""S3-MATCH-013: match_run / match_snapshot / match 三表及约束、索引。

字典一律 VARCHAR + 命名 CHECK（可逆、可演进）；分值 NUMERIC(4,1)；
唯一约束 uq_match_run_target (run_id, target_type, target_id)。

Revision ID: 0001
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _common_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("site_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "match_run",
        *_common_columns(),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("algo_version", sa.String(length=64), nullable=False),
        sa.Column("weight_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column("candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_match_run"),
        sa.CheckConstraint(
            "trigger IN ('publish', 'manual', 'nightly')", name="ck_match_run_trigger"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')", name="ck_match_run_status"
        ),
        sa.CheckConstraint("candidate_count >= 0", name="ck_match_run_candidate_count"),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_match_run_finished_after_start",
        ),
    )
    op.create_index("ix_match_run_org_site_status", "match_run", ["org_id", "site_id", "status"])
    op.create_index("ix_match_run_source_item", "match_run", ["source_item_id"])

    op.create_table(
        "match_snapshot",
        *_common_columns(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("algo_version", sa.String(length=64), nullable=False),
        sa.Column("weight_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_match_snapshot"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["match_run.id"],
            name="fk_match_snapshot_run", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", name="uq_match_snapshot_run"),
    )
    op.create_index("ix_match_snapshot_source_item", "match_snapshot", ["source_item_id"])

    op.create_table(
        "match",
        *_common_columns(),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "factors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "penalties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending_review'"),
            nullable=False,
        ),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.String(length=16), nullable=True),
        sa.Column("reject_note", sa.Text(), nullable=True),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_match"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["match_run.id"], name="fk_match_run", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["match_snapshot.id"],
            name="fk_match_snapshot", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("run_id", "target_type", "target_id", name="uq_match_run_target"),
        sa.CheckConstraint(
            "target_type IN ('company', 'supply_item')", name="ck_match_target_type"
        ),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_match_score_range"),
        sa.CheckConstraint("rank >= 1", name="ck_match_rank"),
        sa.CheckConstraint(
            "status IN ('pending_review', 'adopted', 'rejected', 'later', 'expired')",
            name="ck_match_status",
        ),
        sa.CheckConstraint(
            "reject_reason IS NULL OR reject_reason IN ('不对口', '时机不对', '已有合作', '其他')",
            name="ck_match_reject_reason",
        ),
        sa.CheckConstraint(
            "status NOT IN ('adopted', 'rejected', 'later') "
            "OR (reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_match_review_fields",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR reject_reason IS NOT NULL",
            name="ck_match_rejected_reason",
        ),
    )
    op.create_index("ix_match_status_created", "match", ["status", "created_at"])
    op.create_index("ix_match_source_item", "match", ["source_item_id"])
    op.create_index("ix_match_target_company", "match", ["target_company_id"])
    op.create_index("ix_match_opportunity", "match", ["opportunity_id"])
    op.create_index("ix_match_org_site_status", "match", ["org_id", "site_id", "status"])


def downgrade() -> None:
    op.drop_table("match")
    op.drop_table("match_snapshot")
    op.drop_table("match_run")