"""AC-4：冻结时间测试 —— pending_review 超过 14 天未复核 → expired。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.matches import review_match
from app.tasks.expire import expire_pending_matches
from helpers import NOW, OTHER_ORG_ID, REVIEWER_ID, aged_match, make_match, make_run

BASE_DIR = Path(__file__).resolve().parents[1]
DAY = timedelta(days=1)


def _status(session, match_id) -> str:
    return session.execute(
        text("SELECT status FROM \"match\" WHERE id = :id"), {"id": str(match_id)}
    ).scalar_one()


def _run_task(session, now=NOW, ttl=None):
    kwargs = {"now": now}
    if ttl is not None:
        kwargs["ttl"] = ttl
    ids = expire_pending_matches(session, **kwargs)
    session.commit()
    return ids


# ---------- 14 天边界（冻结时间） ----------


@pytest.mark.parametrize(
    ("age", "should_expire"),
    [
        (timedelta(days=15), True),
        (timedelta(days=14, seconds=1), True),
        (timedelta(days=14), False),  # 恰好 14 天：不算「超过」
        (timedelta(days=13, hours=23), False),
        (timedelta(0), False),
    ],
)
def test_fourteen_day_boundary(session, age, should_expire):
    run = make_run(session)
    match = aged_match(session, run, age)
    session.commit()

    ids = _run_task(session)
    assert (match.id in ids) is should_expire
    assert _status(session, match.id) == ("expired" if should_expire else "pending_review")


def test_expired_row_updated_at_bumped(session):
    run = make_run(session)
    match = aged_match(session, run, 15 * DAY)
    session.commit()

    _run_task(session)
    updated_at = session.execute(
        text("SELECT updated_at FROM \"match\" WHERE id = :id"), {"id": str(match.id)}
    ).scalar_one()
    assert updated_at == NOW


# ---------- 只动 pending_review ----------


@pytest.mark.parametrize("action", ["adopt", "reject", "later"])
def test_reviewed_matches_untouched(session, action):
    run = make_run(session)
    match = aged_match(session, run, 30 * DAY)
    review_match(
        session,
        match,
        action=action,
        reviewer_id=REVIEWER_ID,
        reviewed_at=NOW,
        reject_reason="不对口" if action == "reject" else None,
    )
    session.commit()
    before = _status(session, match.id)

    ids = _run_task(session)
    assert match.id not in ids
    assert _status(session, match.id) == before


def test_already_expired_not_returned_again(session):
    run = make_run(session)
    match = aged_match(session, run, 30 * DAY)
    session.commit()

    first = _run_task(session)
    assert match.id in first
    second = _run_task(session)
    assert second == []
    assert _status(session, match.id) == "expired"


def test_soft_deleted_untouched(session):
    run = make_run(session)
    match = aged_match(session, run, 30 * DAY)
    session.commit()
    session.execute(
        text("UPDATE \"match\" SET deleted_at = :ts WHERE id = :id"),
        {"ts": NOW, "id": str(match.id)},
    )
    session.commit()

    ids = _run_task(session)
    assert match.id not in ids
    assert _status(session, match.id) == "pending_review"


def test_task_is_idempotent(session):
    run = make_run(session)
    aged_match(session, run, 20 * DAY)
    session.commit()
    assert len(_run_task(session)) == 1
    assert _run_task(session) == []


# ---------- 批量 / 跨租户 / 自定义 TTL ----------


def test_mixed_batch_only_stale_pending_expired(session):
    run = make_run(session)
    stale_a = aged_match(session, run, 15 * DAY)
    stale_b = aged_match(session, run, 40 * DAY)
    fresh = make_match(session, run)
    boundary = aged_match(session, run, 14 * DAY)
    session.commit()

    ids = _run_task(session)
    assert set(ids) == {stale_a.id, stale_b.id}
    assert _status(session, fresh.id) == "pending_review"
    assert _status(session, boundary.id) == "pending_review"


def test_expires_across_orgs(session):
    run_a = make_run(session)
    run_b = make_run(session, org_id=OTHER_ORG_ID)
    match_a = aged_match(session, run_a, 15 * DAY)
    match_b = aged_match(session, run_b, 15 * DAY)
    session.commit()

    ids = _run_task(session)
    assert set(ids) == {match_a.id, match_b.id}


def test_custom_ttl(session):
    run = make_run(session)
    eight_days = aged_match(session, run, 8 * DAY)
    six_days = aged_match(session, run, 6 * DAY)
    session.commit()

    ids = _run_task(session, ttl=timedelta(days=7))
    assert ids == [eight_days.id]
    assert _status(session, six_days.id) == "pending_review"


def test_naive_now_rejected(session):
    with pytest.raises(ValueError):
        expire_pending_matches(session, now=datetime(2026, 11, 25, 12, 0, 0))


# ---------- CLI（定时任务入口） ----------


def _cli(database_url: str, *args: str) -> dict:
    env = dict(os.environ, DATABASE_URL=database_url)
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "scripts" / "expire_matches.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BASE_DIR),
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_cli_dry_run_then_real_run(session, database_url):
    run = make_run(session)
    stale = aged_match(session, run, 15 * DAY)
    fresh = make_match(session, run)
    session.commit()

    dry = _cli(database_url, "--now", NOW.isoformat(), "--dry-run")
    assert dry["dry_run"] is True
    assert dry["expired_count"] == 1
    assert dry["match_ids"] == [str(stale.id)]
    assert _status(session, stale.id) == "pending_review"  # dry-run 不写库

    real = _cli(database_url, "--now", NOW.isoformat())
    assert real["dry_run"] is False
    assert real["expired_count"] == 1
    session.expire_all()
    assert _status(session, stale.id) == "expired"
    assert _status(session, fresh.id) == "pending_review"

    again = _cli(database_url, "--now", NOW.isoformat())
    assert again["expired_count"] == 0


def test_cli_naive_now_rejected(database_url):
    env = dict(os.environ, DATABASE_URL=database_url)
    result = subprocess.run(
        [
            sys.executable,
            str(BASE_DIR / "scripts" / "expire_matches.py"),
            "--now",
            "2026-11-25T12:00:00",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BASE_DIR),
        timeout=60,
    )
    assert result.returncode != 0
    assert "时区" in result.stderr