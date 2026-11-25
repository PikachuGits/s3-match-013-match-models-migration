#!/usr/bin/env python
"""过期任务 CLI：pending_review 超过 14 天未复核 → expired。

用法（配置只读环境变量 DATABASE_URL）：
    DATABASE_URL='postgresql+psycopg://USER@127.0.0.1:5432/DB' \
        .venv/bin/python scripts/expire_matches.py [--now 2026-11-25T00:00:00+00:00] \
        [--ttl-days 14] [--dry-run]

建议由 cron / 平台调度器周期调用（如每 30 分钟或每日凌晨），任务幂等。
输出 JSON：cutoff / expired_count / match_ids / dry_run。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from app.db import make_engine, make_session_factory, session_scope  # noqa: E402
from app.models import Match  # noqa: E402
from app.tasks.expire import (  # noqa: E402
    DEFAULT_TTL,
    expire_pending_matches,
    stale_pending_conditions,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="expire stale pending_review matches")
    parser.add_argument("--now", help="冻结/回放时间，ISO8601 且必须带时区偏移")
    parser.add_argument("--ttl-days", type=float, default=None, help="超期天数，默认 14")
    parser.add_argument("--dry-run", action="store_true", help="只列出将被过期的 id，不写库")
    args = parser.parse_args()

    if args.now:
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            parser.error("--now 必须带时区偏移，例如 2026-11-25T00:00:00+00:00")
    else:
        now = datetime.now(timezone.utc)
    ttl = timedelta(days=args.ttl_days) if args.ttl_days is not None else DEFAULT_TTL

    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with session_scope(factory) as session:
            if args.dry_run:
                ids = list(
                    session.execute(
                        select(Match.id).where(*stale_pending_conditions(now, ttl))
                    ).scalars()
                )
            else:
                ids = expire_pending_matches(session, now=now, ttl=ttl)
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "cutoff": (now - ttl).isoformat(),
                "expired_count": len(ids),
                "match_ids": [str(i) for i in ids],
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())