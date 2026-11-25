"""match_run 批次服务。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.enums import RUN_TERMINAL_STATUSES, RUN_TRIGGERS
from app.models import MatchRun
from app.services.errors import MatchValidationError


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise MatchValidationError(f"{field} 必须带时区（timezone-aware）")
    return value


def create_run(
    session: Session,
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    source_item_id: uuid.UUID,
    trigger: str,
    algo_version: str,
    weight_version_id: uuid.UUID | None = None,
    candidate_count: int = 0,
    started_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
) -> MatchRun:
    if trigger not in RUN_TRIGGERS:
        raise MatchValidationError(f"trigger 必须是 {RUN_TRIGGERS} 之一，收到 {trigger!r}")
    if not isinstance(algo_version, str) or not algo_version.strip():
        raise MatchValidationError("algo_version 不能为空")
    if len(algo_version) > 64:
        raise MatchValidationError("algo_version 长度不得超过 64")
    if candidate_count < 0:
        raise MatchValidationError("candidate_count 不得为负")

    run = MatchRun(
        org_id=org_id,
        site_id=site_id,
        source_item_id=source_item_id,
        trigger=trigger,
        algo_version=algo_version,
        weight_version_id=weight_version_id,
        candidate_count=candidate_count,
        started_at=_require_aware(started_at, "started_at") if started_at else _utcnow(),
        created_by=created_by,
    )
    session.add(run)
    session.flush()
    return run


def finish_run(
    session: Session,
    run: MatchRun,
    *,
    status: str,
    finished_at: datetime | None = None,
    candidate_count: int | None = None,
) -> MatchRun:
    if status not in RUN_TERMINAL_STATUSES:
        raise MatchValidationError(f"收尾状态必须是 {RUN_TERMINAL_STATUSES} 之一，收到 {status!r}")
    if run.status != "running":
        raise MatchValidationError(f"批次已收尾（{run.status}），不能重复收尾")
    finished = _require_aware(finished_at, "finished_at") if finished_at else _utcnow()
    if finished < run.started_at:
        raise MatchValidationError("finished_at 不得早于 started_at")
    if candidate_count is not None and candidate_count < 0:
        raise MatchValidationError("candidate_count 不得为负")

    run.status = status
    run.finished_at = finished
    if candidate_count is not None:
        run.candidate_count = candidate_count
    session.flush()
    return run