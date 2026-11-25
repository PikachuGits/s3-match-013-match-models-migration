"""过期任务：pending_review 超过 14 天未复核的推荐置为 expired。

设计要点：
- ``now`` 可注入（冻结时间测试 / 回放），默认取当前 UTC 时间；
- 边界为「严格超过」：created_at < now - ttl 才过期，恰好 14 天不动；
- 只处理未软删除（deleted_at IS NULL）且仍为 pending_review 的行；
- 纯 SQL 批量 UPDATE + RETURNING，幂等，可被 cron / 任意调度器重复调用。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.enums import PENDING_REVIEW_TTL_DAYS
from app.models import Match

DEFAULT_TTL = timedelta(days=PENDING_REVIEW_TTL_DAYS)


def stale_pending_conditions(now: datetime, ttl: timedelta = DEFAULT_TTL) -> tuple:
    """超期未复核的筛选条件，供 UPDATE 与 dry-run SELECT 复用。"""
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now 必须带时区（timezone-aware）")
    cutoff = now - ttl
    return (
        Match.status == "pending_review",
        Match.deleted_at.is_(None),
        Match.created_at < cutoff,
    )


def expire_pending_matches(
    session: Session,
    *,
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> list[uuid.UUID]:
    """把超期未复核的推荐置为 expired，返回本次过期的 match id 列表。

    调用方负责提交事务（建议用 app.db.session_scope）。
    """
    moment = now or datetime.now(timezone.utc)
    stmt = (
        update(Match)
        .where(*stale_pending_conditions(moment, ttl))
        .values(status="expired", updated_at=moment)
        .returning(Match.id)
        .execution_options(synchronize_session=False)
    )
    return [row[0] for row in session.execute(stmt)]