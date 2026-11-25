"""match_snapshot 服务：一个批次固化一份打分输入特征。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatchRun, MatchSnapshot
from app.services.errors import MatchValidationError


def create_snapshot(
    session: Session,
    run: MatchRun,
    *,
    features: dict,
    captured_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
) -> MatchSnapshot:
    if not isinstance(features, dict) or not features:
        raise MatchValidationError("features 必须是非空 JSON 对象（打分输入特征）")
    existing = session.execute(
        select(MatchSnapshot.id).where(MatchSnapshot.run_id == run.id)
    ).first()
    if existing:
        raise MatchValidationError("该批次已存在输入特征快照（run_id 唯一）")
    if captured_at is not None and captured_at.tzinfo is None:
        raise MatchValidationError("captured_at 必须带时区")

    snapshot = MatchSnapshot(
        org_id=run.org_id,
        site_id=run.site_id,
        run_id=run.id,
        source_item_id=run.source_item_id,
        algo_version=run.algo_version,
        weight_version_id=run.weight_version_id,
        features=features,
        captured_at=captured_at or datetime.now(timezone.utc),
        created_by=created_by,
    )
    session.add(snapshot)
    session.flush()
    return snapshot