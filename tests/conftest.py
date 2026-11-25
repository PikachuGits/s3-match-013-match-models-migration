"""共享 pytest fixtures。

测试跑在真实 PostgreSQL 16 上（依赖原生 UUID / JSONB / gen_random_uuid() / CHECK）。
``TEST_DATABASE_URL`` 必填且库名必须含 "test"：session fixture 会重建 public schema，
绝不允许指向共享库。**测试不会自动建库**：目标库须先 ``createdb``，
不存在时直接退出并给出 createdb 提示（见 engine fixture 前置检查）。
"""

from __future__ import annotations

import os
import sys
import uuid as uuid_module
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import models  # noqa: F401,E402  (注册 metadata)
from app.db import Base  # noqa: F401,E402

DELIVERY_TABLES = ("match", "match_snapshot", "match_run")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.exit(
            "TEST_DATABASE_URL is required, e.g. "
            "postgresql+psycopg://USER@127.0.0.1:5432/clawhunt_2010_test",
            returncode=2,
        )
    parsed = make_url(url)
    if "test" not in (parsed.database or "").lower():
        pytest.exit(
            f"Refusing to run destructive fixtures against non-test database {parsed.database!r}",
            returncode=2,
        )
    return url


@pytest.fixture(scope="session")
def roundtrip_database_url(database_url: str):
    """一次性临时库：upgrade/downgrade 往返绝不触碰主测试库。"""

    parsed = make_url(database_url)
    admin_engine = create_engine(
        parsed.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    temp_name = f"clawhunt_2010_roundtrip_{uuid_module.uuid4().hex[:8]}"
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{temp_name}"'))
    url = parsed.set(database=temp_name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{temp_name}" WITH (FORCE)'))
        admin_engine.dispose()


def _alembic_config(url: str) -> Config:
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    return _alembic_config(database_url)


@pytest.fixture(scope="session")
def roundtrip_config(roundtrip_database_url: str) -> Config:
    return _alembic_config(roundtrip_database_url)


@pytest.fixture(scope="session")
def engine(database_url: str, alembic_config: Config):
    """重建 public schema 后应用交付迁移。库不存在时给出 createdb 提示并退出。"""

    eng = create_engine(database_url, poolclass=NullPool, future=True)
    try:
        with eng.connect() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            connection.commit()
    except OperationalError as exc:
        eng.dispose()
        if "does not exist" in str(exc.orig):
            parsed = make_url(database_url)
            pytest.exit(
                f"测试库 {parsed.database!r} 不存在：测试不会自动建库。请先执行 "
                f"createdb -h {parsed.host or '127.0.0.1'} -p {parsed.port or 5432} "
                f"{parsed.database}（库名必须含 test），再重跑测试。",
                returncode=2,
            )
        raise
    command.upgrade(alembic_config, "head")
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_tables(engine):
    """每个测试前后三张交付表均为空。"""

    statement = text(f'TRUNCATE {", ".join(DELIVERY_TABLES)} RESTART IDENTITY CASCADE')
    with engine.begin() as connection:
        connection.execute(statement)
    yield
    with engine.begin() as connection:
        connection.execute(statement)


@pytest.fixture()
def session(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.rollback()
        db.close()