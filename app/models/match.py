"""match：一条匹配推荐（批次内对某个目标公司的供需条目打分结果）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import CommonColumns


class Match(CommonColumns, Base):
    __tablename__ = "match"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("match_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 0–100，一位小数：NUMERIC(4,1) 固定标度 + 范围 CHECK
    score: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    # 各因子：原始值 raw / 归一化分 normalized / 权重 weight / 贡献分 contribution
    factors: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    reasons: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    penalties: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("match_snapshot.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending_review", server_default=text("'pending_review'")
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 字典：不对口 / 时机不对 / 已有合作 / 其他
    reject_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reject_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 商机 id：opportunity 表属其他工单（撮合商机单），不做跨单外键，README 写明对接点
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "target_type", "target_id", name="uq_match_run_target"),
        CheckConstraint(
            "target_type IN ('company', 'supply_item')", name="ck_match_target_type"
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_match_score_range"),
        CheckConstraint("rank >= 1", name="ck_match_rank"),
        CheckConstraint(
            "status IN ('pending_review', 'adopted', 'rejected', 'later', 'expired')",
            name="ck_match_status",
        ),
        CheckConstraint(
            "reject_reason IS NULL OR reject_reason IN ('不对口', '时机不对', '已有合作', '其他')",
            name="ck_match_reject_reason",
        ),
        # 已复核状态必须带复核人与复核时间
        CheckConstraint(
            "status NOT IN ('adopted', 'rejected', 'later') "
            "OR (reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_match_review_fields",
        ),
        # rejected 必须给出字典内原因
        CheckConstraint(
            "status <> 'rejected' OR reject_reason IS NOT NULL",
            name="ck_match_rejected_reason",
        ),
        Index("ix_match_status_created", "status", "created_at"),
        Index("ix_match_source_item", "source_item_id"),
        Index("ix_match_target_company", "target_company_id"),
        Index("ix_match_opportunity", "opportunity_id"),
        Index("ix_match_org_site_status", "org_id", "site_id", "status"),
    )

    run: Mapped["MatchRun"] = relationship("MatchRun", back_populates="matches")  # noqa: F821
    snapshot: Mapped["MatchSnapshot | None"] = relationship("MatchSnapshot")  # noqa: F821