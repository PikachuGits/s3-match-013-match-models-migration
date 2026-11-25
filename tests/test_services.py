"""服务层校验与状态机：字典、分值、结构、唯一性前置检查、复核流转。"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.enums import REJECT_REASONS
from app.services.errors import MatchValidationError
from app.services.matches import add_match, add_matches, review_match, validate_score
from app.services.runs import finish_run
from app.services.snapshots import create_snapshot
from helpers import (
    NOW,
    ORG_ID,
    REVIEWER_ID,
    SITE_ID,
    factor,
    make_match,
    make_run,
    make_snapshot,
    snapshot_features,
)

# ---------- 批次 ----------


def test_create_run_defaults(session):
    run = make_run(session)
    assert run.status == "running"
    assert run.candidate_count == 0
    assert run.started_at.tzinfo is not None
    assert run.org_id == ORG_ID and run.site_id == SITE_ID


@pytest.mark.parametrize("trigger", ["publish", "manual", "nightly"])
def test_create_run_valid_triggers(session, trigger):
    run = make_run(session, trigger=trigger)
    assert run.trigger == trigger


@pytest.mark.parametrize(
    "overrides",
    [
        {"trigger": "bogus"},
        {"algo_version": ""},
        {"algo_version": "   "},
        {"algo_version": "x" * 65},
        {"candidate_count": -1},
    ],
)
def test_create_run_invalid(session, overrides):
    with pytest.raises(MatchValidationError):
        make_run(session, **overrides)


def test_finish_run_done(session):
    run = make_run(session, started_at=NOW)
    finished = finish_run(
        session, run, status="done", finished_at=NOW + timedelta(minutes=5), candidate_count=12
    )
    assert finished.status == "done"
    assert finished.finished_at == NOW + timedelta(minutes=5)
    assert finished.candidate_count == 12


def test_finish_run_failed(session):
    run = make_run(session)
    assert finish_run(session, run, status="failed").status == "failed"


def test_finish_run_twice_rejected(session):
    run = make_run(session)
    finish_run(session, run, status="done")
    with pytest.raises(MatchValidationError):
        finish_run(session, run, status="failed")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "running"},
        {"status": "bogus"},
        {"status": "done", "finished_at": NOW - timedelta(days=365)},
        {"status": "done", "candidate_count": -1},
    ],
)
def test_finish_run_invalid(session, kwargs):
    run = make_run(session, started_at=NOW)
    with pytest.raises(MatchValidationError):
        finish_run(session, run, **kwargs)


# ---------- 快照 ----------


def test_snapshot_copies_run_context(session):
    run = make_run(session, weight_version_id=uuid.uuid4())
    features = snapshot_features()
    snapshot = make_snapshot(session, run, features=features)
    assert snapshot.run_id == run.id
    assert snapshot.source_item_id == run.source_item_id
    assert snapshot.algo_version == run.algo_version
    assert snapshot.weight_version_id == run.weight_version_id
    assert snapshot.features == features


@pytest.mark.parametrize("features", [{}, [], None, "x", 42])
def test_snapshot_features_must_be_nonempty_dict(session, features):
    run = make_run(session)
    with pytest.raises(MatchValidationError):
        create_snapshot(session, run, features=features)


def test_snapshot_duplicate_service_rejected(session):
    run = make_run(session)
    make_snapshot(session, run)
    with pytest.raises(MatchValidationError):
        make_snapshot(session, run)


# ---------- 推荐写入 ----------


def test_add_match_persists_with_context(session):
    run = make_run(session)
    snapshot = make_snapshot(session, run)
    match = make_match(session, run, target_company_id=uuid.uuid4())
    assert match.run_id == run.id
    assert match.source_item_id == run.source_item_id
    assert match.org_id == run.org_id and match.site_id == run.site_id
    assert match.status == "pending_review"
    assert match.snapshot_id == snapshot.id
    assert match.score == Decimal("80.0")
    assert match.factors[0]["name"] == "industry_fit"


def test_add_match_without_snapshot(session):
    run = make_run(session)
    match = make_match(session, run)
    assert match.snapshot_id is None


def test_add_match_duplicate_service_rejected(session):
    run = make_run(session)
    first = make_match(session, run)
    with pytest.raises(MatchValidationError):
        make_match(session, run, target_type=first.target_type, target_id=first.target_id)


def test_add_match_snapshot_from_other_run_rejected(session):
    run_a = make_run(session)
    run_b = make_run(session)
    snapshot_b = make_snapshot(session, run_b)
    with pytest.raises(MatchValidationError):
        make_match(session, run_a, snapshot_id=snapshot_b.id)


def test_add_match_unknown_snapshot_rejected(session):
    run = make_run(session)
    with pytest.raises(MatchValidationError):
        make_match(session, run, snapshot_id=uuid.uuid4())


def test_add_match_created_at_override(session):
    run = make_run(session)
    stamp = NOW - timedelta(days=15)
    match = make_match(session, run, created_at=stamp)
    assert match.created_at == stamp


def test_add_matches_batch(session):
    run = make_run(session)
    payloads = [
        dict(target_type="company", target_id=uuid.uuid4(), score=Decimal("90.0"), rank=1),
        dict(target_type="company", target_id=uuid.uuid4(), score=Decimal("80.0"), rank=2),
        dict(target_type="supply_item", target_id=uuid.uuid4(), score=Decimal("70.5"), rank=3),
    ]
    matches = add_matches(session, run, payloads)
    assert len(matches) == 3
    assert [m.rank for m in matches] == [1, 2, 3]


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_type": "bogus"},
        {"rank": 0},
        {"rank": True},
        {"rank": 1.5},
        {"score": Decimal("100.1")},
        {"score": Decimal("-0.1")},
        {"score": Decimal("87.55")},
        {"score": "abc"},
        {"score": True},
        {"score": None},
    ],
)
def test_add_match_invalid_params(session, overrides):
    run = make_run(session)
    with pytest.raises(MatchValidationError):
        make_match(session, run, **overrides)


@pytest.mark.parametrize(
    "factors",
    [
        "not-a-list",
        [factor() | {"normalized": 1.2}],
        [factor() | {"weight": -0.1}],
        [factor() | {"name": ""}],
        [factor() | {"raw": True}],
        [{"name": "x"}],
        ["x"],
    ],
)
def test_add_match_invalid_factors(session, factors):
    run = make_run(session)
    with pytest.raises(MatchValidationError):
        make_match(session, run, factors=factors)


@pytest.mark.parametrize("reasons", ["x", [""], [3]])
def test_add_match_invalid_reasons(session, reasons):
    run = make_run(session)
    with pytest.raises(MatchValidationError):
        make_match(session, run, reasons=reasons)


@pytest.mark.parametrize(
    "penalties",
    [
        "x",
        [{"code": "dup", "points": -1}],
        [{"points": 1}],
        [{"code": "", "points": 1}],
        [{"code": "dup", "points": True}],
    ],
)
def test_add_match_invalid_penalties(session, penalties):
    run = make_run(session)
    with pytest.raises(MatchValidationError):
        make_match(session, run, penalties=penalties)


@pytest.mark.parametrize(
    "score", [0, 100, 87.5, Decimal("0.1"), "33.3", "100.0"]
)
def test_validate_score_valid(score):
    assert validate_score(score) == Decimal(str(score)).quantize(Decimal("0.1"))


@pytest.mark.parametrize(
    "score", [87.55, -0.1, 100.1, "x", None, True, float("nan"), float("inf")]
)
def test_validate_score_invalid(score):
    with pytest.raises(MatchValidationError):
        validate_score(score)


# ---------- 复核状态机 ----------


def test_review_adopt(session):
    run = make_run(session)
    match = make_match(session, run)
    opportunity_id = uuid.uuid4()
    reviewed = review_match(
        session,
        match,
        action="adopt",
        reviewer_id=REVIEWER_ID,
        reviewed_at=NOW,
        opportunity_id=opportunity_id,
    )
    assert reviewed.status == "adopted"
    assert reviewed.reviewer_id == REVIEWER_ID
    assert reviewed.reviewed_at == NOW
    assert reviewed.opportunity_id == opportunity_id
    assert reviewed.reject_reason is None


@pytest.mark.parametrize("reason", REJECT_REASONS)
def test_review_reject_dictionary_reasons(session, reason):
    run = make_run(session)
    match = make_match(session, run)
    reviewed = review_match(
        session,
        match,
        action="reject",
        reviewer_id=REVIEWER_ID,
        reject_reason=reason,
        reject_note="虚构备注",
    )
    assert reviewed.status == "rejected"
    assert reviewed.reject_reason == reason
    assert reviewed.reject_note == "虚构备注"


@pytest.mark.parametrize("reason", ["瞎写", "", None, "价格太高"])
def test_review_reject_invalid_reason(session, reason):
    run = make_run(session)
    match = make_match(session, run)
    with pytest.raises(MatchValidationError):
        review_match(
            session, match, action="reject", reviewer_id=REVIEWER_ID, reject_reason=reason
        )
    assert match.status == "pending_review"


def test_review_later_then_adopt(session):
    run = make_run(session)
    match = make_match(session, run)
    review_match(session, match, action="later", reviewer_id=REVIEWER_ID)
    assert match.status == "later"
    review_match(session, match, action="adopt", reviewer_id=REVIEWER_ID)
    assert match.status == "adopted"


@pytest.mark.parametrize("action", ["adopt", "reject", "later"])
def test_review_terminal_states_rejected(session, action):
    run = make_run(session)
    match = make_match(session, run)
    review_match(session, match, action="adopt", reviewer_id=REVIEWER_ID)
    with pytest.raises(MatchValidationError):
        review_match(
            session,
            match,
            action=action,
            reviewer_id=REVIEWER_ID,
            reject_reason="不对口" if action == "reject" else None,
        )


def test_review_expired_rejected(session):
    run = make_run(session)
    match = make_match(session, run)
    session.commit()
    session.execute(
        text("UPDATE \"match\" SET status = 'expired' WHERE id = :id"), {"id": str(match.id)}
    )
    session.commit()
    session.expire_all()
    with pytest.raises(MatchValidationError):
        review_match(session, match, action="adopt", reviewer_id=REVIEWER_ID)


def test_review_requires_reviewer(session):
    run = make_run(session)
    match = make_match(session, run)
    with pytest.raises(MatchValidationError):
        review_match(session, match, action="adopt", reviewer_id=None)


def test_review_invalid_action(session):
    run = make_run(session)
    match = make_match(session, run)
    with pytest.raises(MatchValidationError):
        review_match(session, match, action="bogus", reviewer_id=REVIEWER_ID)


def test_review_adopt_cannot_carry_reject_reason(session):
    run = make_run(session)
    match = make_match(session, run)
    with pytest.raises(MatchValidationError):
        review_match(
            session,
            match,
            action="adopt",
            reviewer_id=REVIEWER_ID,
            reject_reason="不对口",
        )


def test_review_later_cannot_carry_opportunity(session):
    run = make_run(session)
    match = make_match(session, run)
    with pytest.raises(MatchValidationError):
        review_match(
            session,
            match,
            action="later",
            reviewer_id=REVIEWER_ID,
            opportunity_id=uuid.uuid4(),
        )


def test_add_match_naive_created_at_rejected(session):
    run = make_run(session)
    with pytest.raises(MatchValidationError):
        make_match(session, run, created_at=NOW.replace(tzinfo=None))