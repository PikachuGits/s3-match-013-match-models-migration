"""match 推荐写入与复核服务。

- 写入：字典 / 分值范围与标度 / rank / factors-reasons-penalties 结构前置校验，
  唯一性 (run_id, target_type, target_id) 由数据库约束兜底。
- 复核：pending_review / later → adopted / rejected / later；
  rejected 必须给出字典内 reject_reason；adopted / rejected / expired 为终态。
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import REJECT_REASONS, REVIEW_ACTIONS, REVIEWABLE_STATUSES, TARGET_TYPES
from app.models import Match, MatchRun, MatchSnapshot
from app.services.errors import MatchValidationError

_FACTOR_KEYS = ("name", "raw", "normalized", "weight", "contribution")
_ONE_DECIMAL = Decimal("0.1")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, Decimal)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def validate_score(score: object) -> Decimal:
    """0–100 且最多一位小数；返回 Decimal。"""
    if isinstance(score, bool) or not isinstance(score, (int, float, Decimal, str)):
        raise MatchValidationError("score 必须是数值")
    try:
        value = Decimal(str(score))
    except InvalidOperation as exc:
        raise MatchValidationError(f"score 不是合法数值：{score!r}") from exc
    if not value.is_finite():
        raise MatchValidationError("score 必须是有限数值")
    if value < 0 or value > 100:
        raise MatchValidationError("score 必须在 0–100 之间")
    if value != value.quantize(_ONE_DECIMAL):
        raise MatchValidationError("score 最多保留一位小数")
    return value


def _validate_factors(factors: object) -> None:
    if not isinstance(factors, list):
        raise MatchValidationError("factors 必须是 JSON 数组")
    for entry in factors:
        if not isinstance(entry, dict):
            raise MatchValidationError("factors 每项必须是对象")
        missing = [key for key in _FACTOR_KEYS if key not in entry]
        if missing:
            raise MatchValidationError(f"factors 项缺少字段：{missing}")
        if not isinstance(entry["name"], str) or not entry["name"].strip():
            raise MatchValidationError("factors.name 必须是非空字符串")
        for key in ("raw", "normalized", "weight", "contribution"):
            if not _is_number(entry[key]):
                raise MatchValidationError(f"factors.{key} 必须是数值")
        if not 0 <= entry["normalized"] <= 1:
            raise MatchValidationError("factors.normalized 必须在 0–1 之间")
        if not 0 <= entry["weight"] <= 1:
            raise MatchValidationError("factors.weight 必须在 0–1 之间")


def _validate_reasons(reasons: object) -> None:
    if not isinstance(reasons, list):
        raise MatchValidationError("reasons 必须是 JSON 数组")
    for item in reasons:
        if not isinstance(item, str) or not item.strip():
            raise MatchValidationError("reasons 每项必须是非空字符串")


def _validate_penalties(penalties: object) -> None:
    if not isinstance(penalties, list):
        raise MatchValidationError("penalties 必须是 JSON 数组")
    for entry in penalties:
        if not isinstance(entry, dict):
            raise MatchValidationError("penalties 每项必须是对象")
        if not isinstance(entry.get("code"), str) or not entry["code"].strip():
            raise MatchValidationError("penalties.code 必须是非空字符串")
        if not _is_number(entry.get("points")) or entry["points"] < 0:
            raise MatchValidationError("penalties.points 必须是非负数值（扣分量）")


def add_match(
    session: Session,
    run: MatchRun,
    *,
    target_type: str,
    target_id: uuid.UUID,
    score: object,
    rank: int,
    target_company_id: uuid.UUID | None = None,
    factors: list | None = None,
    reasons: list | None = None,
    penalties: list | None = None,
    snapshot_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> Match:
    if target_type not in TARGET_TYPES:
        raise MatchValidationError(f"target_type 必须是 {TARGET_TYPES} 之一，收到 {target_type!r}")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise MatchValidationError("rank 必须是 >= 1 的整数")
    score_value = validate_score(score)
    factors = [] if factors is None else factors
    reasons = [] if reasons is None else reasons
    penalties = [] if penalties is None else penalties
    _validate_factors(factors)
    _validate_reasons(reasons)
    _validate_penalties(penalties)
    if created_at is not None and created_at.tzinfo is None:
        raise MatchValidationError("created_at 必须带时区")

    duplicate = session.execute(
        select(Match.id).where(
            Match.run_id == run.id,
            Match.target_type == target_type,
            Match.target_id == target_id,
        )
    ).first()
    if duplicate:
        raise MatchValidationError("同一批次同一目标已存在推荐（uq_match_run_target）")

    if snapshot_id is None:
        snapshot_id = run.snapshot.id if run.snapshot is not None else None
    else:
        snapshot = session.get(MatchSnapshot, snapshot_id)
        if snapshot is None or snapshot.run_id != run.id:
            raise MatchValidationError("snapshot_id 不存在或不属于该批次")

    match = Match(
        org_id=run.org_id,
        site_id=run.site_id,
        run_id=run.id,
        source_item_id=run.source_item_id,
        target_type=target_type,
        target_id=target_id,
        target_company_id=target_company_id,
        score=score_value,
        rank=rank,
        factors=factors,
        reasons=reasons,
        penalties=penalties,
        snapshot_id=snapshot_id,
        created_by=created_by,
    )
    if created_at is not None:
        match.created_at = created_at
    session.add(match)
    session.flush()
    return match


def add_matches(session: Session, run: MatchRun, payloads: list[dict]) -> list[Match]:
    """批量写入一个批次的推荐列表。"""
    return [add_match(session, run, **payload) for payload in payloads]


def review_match(
    session: Session,
    match: Match,
    *,
    action: str,
    reviewer_id: uuid.UUID,
    reviewed_at: datetime | None = None,
    reject_reason: str | None = None,
    reject_note: str | None = None,
    opportunity_id: uuid.UUID | None = None,
) -> Match:
    if action not in REVIEW_ACTIONS:
        raise MatchValidationError(
            f"action 必须是 {tuple(REVIEW_ACTIONS)} 之一，收到 {action!r}"
        )
    if match.status not in REVIEWABLE_STATUSES:
        raise MatchValidationError(f"状态 {match.status} 不允许复核（终态或已过期）")
    if reviewer_id is None:
        raise MatchValidationError("复核必须记录 reviewer_id")
    if reviewed_at is not None and reviewed_at.tzinfo is None:
        raise MatchValidationError("reviewed_at 必须带时区")

    target_status = REVIEW_ACTIONS[action]
    if target_status == "rejected":
        if reject_reason not in REJECT_REASONS:
            raise MatchValidationError(
                f"reject_reason 必须在字典 {REJECT_REASONS} 内，收到 {reject_reason!r}"
            )
    else:
        if reject_reason is not None or reject_note is not None:
            raise MatchValidationError("仅 rejected 可携带 reject_reason / reject_note")
        if action != "adopt" and opportunity_id is not None:
            raise MatchValidationError("仅 adopted 可关联 opportunity_id")

    match.status = target_status
    match.reviewer_id = reviewer_id
    match.reviewed_at = reviewed_at or _utcnow()
    match.reject_reason = reject_reason if target_status == "rejected" else None
    match.reject_note = reject_note if target_status == "rejected" else None
    if target_status == "adopted" and opportunity_id is not None:
        match.opportunity_id = opportunity_id
    session.flush()
    return match