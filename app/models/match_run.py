"""match_run：一次匹配批次（对某个供需条目跑一轮匹配打分）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import CommonColumns


class MatchRun(CommonColumns, Base):
    __tablename__ = "match_run"

    # 供需条目 id：属其他工单的表，本单不做跨单外键，README 写明对接点
    source_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    algo_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # 权重版本 id：权重配置表属其他工单，接口桩对接
    weight_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default=text("'running'")
    )
    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "trigger IN ('publish', 'manual', 'nightly')", name="ck_match_run_trigger"
        ),
        CheckConstraint(
            "status IN ('running', 'done', 'failed')", name="ck_match_run_status"
        ),
        CheckConstraint("candidate_count >= 0", name="ck_match_run_candidate_count"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_match_run_finished_after_start",
        ),
        Index("ix_match_run_org_site_status", "org_id", "site_id", "status"),
        Index("ix_match_run_source_item", "source_item_id"),
    )

    matches: Mapped[list["Match"]] = relationship(  # noqa: F821
        "Match", back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    snapshot: Mapped["MatchSnapshot | None"] = relationship(  # noqa: F821
        "MatchSnapshot",
        back_populates="run",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )