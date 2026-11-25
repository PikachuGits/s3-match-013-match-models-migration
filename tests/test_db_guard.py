"""测试库前置守卫（操作方复验发现的注意点，固化为自动化测试）：

1. 库名不含 test → 硬拦截（防止对共享库做破坏性重建）；
2. 库不存在 → 直接退出并提示 createdb，而不是让全部用例报 error。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parents[1]


def _pytest_with_url(url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, TEST_DATABASE_URL=url)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_migration.py::test_upgrade_creates_three_tables",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BASE_DIR),
        timeout=120,
    )


def test_non_test_db_name_hard_rejected(database_url):
    url = (
        make_url(database_url)
        .set(database="clawhunt_2010_prod")
        .render_as_string(hide_password=False)
    )
    result = _pytest_with_url(url)
    assert result.returncode == 2
    assert "non-test" in result.stdout + result.stderr


def test_missing_db_exits_with_createdb_hint(database_url):
    url = (
        make_url(database_url)
        .set(database="clawhunt_2010_missing_test")
        .render_as_string(hide_password=False)
    )
    result = _pytest_with_url(url)
    combined = result.stdout + result.stderr
    assert result.returncode == 2
    assert "createdb" in combined
    assert "clawhunt_2010_missing_test" in combined