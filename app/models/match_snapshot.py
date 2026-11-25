"""match_snapshot：存放本次打分用到的输入特征（一个批次一份，run_id 唯一）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import CommonColumns


class MatchSnapshot(CommonColumns, Base):
    __tablename__ = "match_snapshot"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("match_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    algo_version: Mapped[str] = mapped_column(String(64), nullable=False)
    weight_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 输入特征 JSON：建议包含 source_item / candidates / weights 三块，见 README
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_match_snapshot_run"),
        Index("ix_match_snapshot_source_item", "source_item_id"),
    )

    run: Mapped["MatchRun"] = relationship("MatchRun", back_populates="snapshot")  # noqa: F821