"""数据库层约束直证（绕过服务层）：AC-2 字典、AC-3 唯一、范围/状态机/外键。"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.enums import REJECT_REASONS
from app.models import MatchSnapshot
from helpers import NOW, REVIEWER_ID, make_match, make_run, make_snapshot, raw_match


def _assert_integrity_error(session, obj, constraint: str):
    session.add(obj)
    with pytest.raises(IntegrityError) as exc:
        session.flush()
    assert constraint in str(exc.value.orig)
    session.rollback()


def _assert_sql_integrity_error(session, statement: str, params: dict, constraint: str):
    with pytest.raises(IntegrityError) as exc:
        session.execute(text(statement), params)
    assert constraint in str(exc.value.orig)
    session.rollback()


# ---------- AC-3：(run_id, target_type, target_id) 唯一 ----------


def test_duplicate_target_in_same_run_rejected(session):
    run = make_run(session)
    first = make_match(session, run)
    session.commit()

    duplicate = raw_match(run, target_type=first.target_type, target_id=first.target_id)
    _assert_integrity_error(session, duplicate, "uq_match_run_target")


def test_same_target_different_type_allowed(session):
    run = make_run(session)
    first = make_match(session, run, target_type="company")
    session.commit()

    other = raw_match(run, target_type="supply_item", target_id=first.target_id)
    session.add(other)
    session.flush()
    assert other.id != first.id


def test_same_target_different_run_allowed(session):
    run_a = make_run(session)
    run_b = make_run(session)
    first = make_match(session, run_a)
    session.commit()

    other = raw_match(run_b, target_type=first.target_type, target_id=first.target_id)
    session.add(other)
    session.flush()
    assert other.id != first.id


# ---------- AC-2：reject_reason 字典 ----------


@pytest.mark.parametrize("bad_reason", ["瞎写", "not_in_dict", "", "不对口 ", "价格太高"])
def test_invalid_reject_reason_rejected_by_db(session, bad_reason):
    run = make_run(session)
    session.commit()
    bad = raw_match(run, reject_reason=bad_reason)
    _assert_integrity_error(session, bad, "ck_match_reject_reason")


@pytest.mark.parametrize("reason", REJECT_REASONS)
def test_dictionary_reject_reason_accepted(session, reason):
    run = make_run(session)
    session.commit()
    match = raw_match(
        run,
        status="rejected",
        reject_reason=reason,
        reviewer_id=REVIEWER_ID,
        reviewed_at=NOW,
    )
    session.add(match)
    session.flush()
    assert match.reject_reason == reason


def test_null_reject_reason_allowed(session):
    run = make_run(session)
    session.commit()
    match = raw_match(run, reject_reason=None)
    session.add(match)
    session.flush()
    assert match.reject_reason is None


def test_rejected_without_reason_rejected_by_db(session):
    run = make_run(session)
    session.commit()
    match = raw_match(
        run, status="rejected", reviewer_id=REVIEWER_ID, reviewed_at=NOW, reject_reason=None
    )
    _assert_integrity_error(session, match, "ck_match_rejected_reason")


def test_reviewed_status_without_reviewer_rejected_by_db(session):
    run = make_run(session)
    session.commit()
    match = raw_match(run, status="adopted", reviewer_id=None, reviewed_at=None)
    _assert_integrity_error(session, match, "ck_match_review_fields")


# ---------- 字典 / 范围 / 状态 ----------


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("trigger", "bogus", "ck_match_run_trigger"),
        ("status", "bogus", "ck_match_run_status"),
        ("candidate_count", -1, "ck_match_run_candidate_count"),
    ],
)
def test_match_run_check_constraints(session, column, value, constraint):
    run = make_run(session)
    session.commit()
    _assert_sql_integrity_error(
        session,
        f"UPDATE match_run SET {column} = :value WHERE id = :id",
        {"value": value, "id": str(run.id)},
        constraint,
    )


def test_finished_before_started_rejected(session):
    run = make_run(session, started_at=NOW)
    session.commit()
    _assert_sql_integrity_error(
        session,
        "UPDATE match_run SET finished_at = :finished WHERE id = :id",
        {"finished": NOW - timedelta(hours=1), "id": str(run.id)},
        "ck_match_run_finished_after_start",
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"target_type": "bogus"}, "ck_match_target_type"),
        ({"status": "bogus"}, "ck_match_status"),
        ({"rank": 0}, "ck_match_rank"),
        ({"rank": -3}, "ck_match_rank"),
        ({"score": Decimal("-0.1")}, "ck_match_score_range"),
        ({"score": Decimal("100.1")}, "ck_match_score_range"),
    ],
)
def test_match_check_constraints(session, overrides, constraint):
    run = make_run(session)
    session.commit()
    _assert_integrity_error(session, raw_match(run, **overrides), constraint)


@pytest.mark.parametrize("score", [Decimal("0.0"), Decimal("100.0"), Decimal("87.5")])
def test_score_boundaries_and_scale(session, score):
    run = make_run(session)
    session.commit()
    match = raw_match(run, score=score)
    session.add(match)
    session.flush()
    stored = session.execute(
        text("SELECT score FROM \"match\" WHERE id = :id"), {"id": str(match.id)}
    ).scalar_one()
    assert stored == score


def test_score_scale_is_one_decimal_in_db(session):
    """NUMERIC(4,1) 固定一位小数：超精度入库被数据库舍入（服务层则直接拒绝）。"""
    run = make_run(session)
    session.commit()
    match = raw_match(run, score=Decimal("87.55"))
    session.add(match)
    session.flush()
    stored = session.execute(
        text("SELECT score FROM \"match\" WHERE id = :id"), {"id": str(match.id)}
    ).scalar_one()
    assert stored == Decimal("87.6")


# ---------- 外键 / 级联 / 快照唯一 ----------


def test_match_requires_existing_run(session):
    run = make_run(session)
    session.commit()
    orphan = raw_match(run, run_id=uuid.uuid4())
    _assert_integrity_error(session, orphan, "fk_match_run")


def test_match_snapshot_fk_enforced(session):
    run = make_run(session)
    session.commit()
    bad = raw_match(run, snapshot_id=uuid.uuid4())
    _assert_integrity_error(session, bad, "fk_match_snapshot")


def test_snapshot_unique_per_run_enforced(session):
    run = make_run(session)
    make_snapshot(session, run)
    session.commit()
    duplicate = MatchSnapshot(
        org_id=run.org_id,
        site_id=run.site_id,
        run_id=run.id,
        source_item_id=run.source_item_id,
        algo_version=run.algo_version,
        features={"again": True},
    )
    _assert_integrity_error(session, duplicate, "uq_match_snapshot_run")


def test_delete_run_cascades_matches_and_snapshot(session):
    run = make_run(session)
    make_snapshot(session, run)
    make_match(session, run)
    session.commit()

    session.execute(text("DELETE FROM match_run WHERE id = :id"), {"id": str(run.id)})
    session.commit()
    assert session.execute(text("SELECT count(*) FROM \"match\"")).scalar_one() == 0
    assert session.execute(text("SELECT count(*) FROM match_snapshot")).scalar_one() == 0


def test_delete_snapshot_sets_match_snapshot_id_null(session):
    run = make_run(session)
    snapshot = make_snapshot(session, run)
    match = make_match(session, run)
    session.commit()
    assert match.snapshot_id == snapshot.id

    session.execute(
        text("DELETE FROM match_snapshot WHERE id = :id"), {"id": str(snapshot.id)}
    )
    session.commit()
    stored = session.execute(
        text("SELECT snapshot_id FROM \"match\" WHERE id = :id"), {"id": str(match.id)}
    ).scalar_one()
    assert stored is None


def test_soft_delete_column_writable(session):
    run = make_run(session)
    match = make_match(session, run)
    session.commit()
    session.execute(
        text("UPDATE \"match\" SET deleted_at = :ts WHERE id = :id"),
        {"ts": NOW, "id": str(match.id)},
    )
    session.commit()
    stored = session.execute(
        text("SELECT deleted_at FROM \"match\" WHERE id = :id"), {"id": str(match.id)}
    ).scalar_one()
    assert stored is not None