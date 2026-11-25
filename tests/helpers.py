"""测试数据构造器：全部为虚构数据（统一约定：不使用任何真实个人/企业信息）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Match
from app.services.matches import add_match
from app.services.runs import create_run
from app.services.snapshots import create_snapshot

ORG_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SITE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
OTHER_ORG_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
REVIEWER_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")

# 冻结时间基准（AC-4）
NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def make_run(session, **overrides):
    params = dict(
        org_id=ORG_ID,
        site_id=SITE_ID,
        source_item_id=uuid.uuid4(),
        trigger="manual",
        algo_version="match-v1.0.0",
    )
    params.update(overrides)
    return create_run(session, **params)


def factor(name="industry_fit", raw=12.0, normalized=0.8, weight=0.5, contribution=40.0):
    return {
        "name": name,
        "raw": raw,
        "normalized": normalized,
        "weight": weight,
        "contribution": contribution,
    }


def snapshot_features():
    return {
        "source_item": {"item_id": str(uuid.uuid4()), "kind": "demand", "industry": "虚构行业A"},
        "candidates": [{"company_id": str(uuid.uuid4()), "name": "虚构企业甲"}],
        "weights": {"industry_fit": 0.5, "region_fit": 0.3, "history_fit": 0.2},
    }


def make_snapshot(session, run, **overrides):
    params = dict(features=snapshot_features())
    params.update(overrides)
    return create_snapshot(session, run, **params)


def make_match(session, run, **overrides):
    params = dict(
        target_type="company",
        target_id=uuid.uuid4(),
        score=Decimal("80.0"),
        rank=1,
        factors=[factor()],
        reasons=["虚构理由：行业对口"],
        penalties=[],
    )
    params.update(overrides)
    return add_match(session, run, **params)


def raw_match(run, **overrides) -> Match:
    """绕过服务层直接构造 ORM 对象，用于验证数据库约束本身。"""
    params = dict(
        org_id=run.org_id,
        site_id=run.site_id,
        run_id=run.id,
        source_item_id=run.source_item_id,
        target_type="company",
        target_id=uuid.uuid4(),
        score=Decimal("80.0"),
        rank=1,
        factors=[],
        reasons=[],
        penalties=[],
        status="pending_review",
    )
    params.update(overrides)
    return Match(**params)


def aged_match(session, run, age: timedelta, **overrides):
    """created_at = NOW - age 的推荐（冻结时间测试用）。"""
    overrides.setdefault("created_at", NOW - age)
    return make_match(session, run, **overrides)