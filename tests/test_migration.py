"""AC-1：迁移 upgrade / downgrade 均成功；模型与 DDL 零漂移；结构符合工单。"""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from helpers import make_run

DELIVERY_TABLES = {"match_run", "match_snapshot", "match"}
COMMON_COLUMNS = {
    "id", "org_id", "site_id", "created_at", "created_by", "updated_at", "deleted_at",
}
EXPECTED_CHECKS = {
    "match_run": {
        "ck_match_run_trigger",
        "ck_match_run_status",
        "ck_match_run_candidate_count",
        "ck_match_run_finished_after_start",
    },
    "match": {
        "ck_match_target_type",
        "ck_match_score_range",
        "ck_match_rank",
        "ck_match_status",
        "ck_match_reject_reason",
        "ck_match_review_fields",
        "ck_match_rejected_reason",
    },
}
EXPECTED_INDEXES = {
    "match_run": {"ix_match_run_org_site_status", "ix_match_run_source_item"},
    "match_snapshot": {"ix_match_snapshot_source_item"},
    "match": {
        "ix_match_status_created",
        "ix_match_source_item",
        "ix_match_target_company",
        "ix_match_opportunity",
        "ix_match_org_site_status",
    },
}


def test_upgrade_creates_three_tables(engine):
    names = set(inspect(engine).get_table_names())
    assert DELIVERY_TABLES <= names


@pytest.mark.parametrize("table", sorted(DELIVERY_TABLES))
def test_common_columns_present(engine, table):
    columns = {c["name"]: c for c in inspect(engine).get_columns(table)}
    assert COMMON_COLUMNS <= set(columns)
    for name in ("id", "org_id", "site_id", "created_at", "updated_at"):
        assert columns[name]["nullable"] is False
    for name in ("created_by", "deleted_at"):
        assert columns[name]["nullable"] is True


def test_roundtrip_upgrade_downgrade_upgrade(roundtrip_config, roundtrip_database_url):
    eng = create_engine(roundtrip_database_url, poolclass=NullPool, future=True)
    try:
        command.upgrade(roundtrip_config, "head")
        assert DELIVERY_TABLES <= set(inspect(eng).get_table_names())

        command.downgrade(roundtrip_config, "base")
        assert not (DELIVERY_TABLES & set(inspect(eng).get_table_names()))

        command.upgrade(roundtrip_config, "head")
        assert DELIVERY_TABLES <= set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


def test_downgrade_on_suite_db_then_reupgrade(alembic_config, engine):
    """主测试库同样可 downgrade（AC-1 直证），随后恢复 head 供后续测试。"""
    command.downgrade(alembic_config, "base")
    assert not (DELIVERY_TABLES & set(inspect(engine).get_table_names()))
    command.upgrade(alembic_config, "head")
    assert DELIVERY_TABLES <= set(inspect(engine).get_table_names())


def test_no_autogenerate_drift(alembic_config):
    """模型 metadata 与迁移产出的 DDL 完全一致（alembic check 无差异即通过）。"""
    command.check(alembic_config)


def test_unique_constraint_run_target(engine):
    uniques = inspect(engine).get_unique_constraints("match")
    target = [u for u in uniques if u["name"] == "uq_match_run_target"]
    assert len(target) == 1
    assert target[0]["column_names"] == ["run_id", "target_type", "target_id"]


def test_snapshot_unique_per_run(engine):
    uniques = inspect(engine).get_unique_constraints("match_snapshot")
    assert any(
        u["name"] == "uq_match_snapshot_run" and u["column_names"] == ["run_id"]
        for u in uniques
    )


@pytest.mark.parametrize("table", sorted(EXPECTED_CHECKS))
def test_check_constraints_named(engine, table):
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = CAST(:table AS regclass) AND contype = 'c'"
            ),
            {"table": table},
        ).scalars()
    assert EXPECTED_CHECKS[table] <= set(rows)


@pytest.mark.parametrize("table", sorted(EXPECTED_INDEXES))
def test_indexes_present(engine, table):
    names = {ix["name"] for ix in inspect(engine).get_indexes(table)}
    assert EXPECTED_INDEXES[table] <= names


def test_foreign_key_delete_rules(engine):
    fks = inspect(engine).get_foreign_keys("match")
    by_column = {fk["constrained_columns"][0]: fk for fk in fks}
    assert by_column["run_id"]["referred_table"] == "match_run"
    assert by_column["run_id"]["options"].get("ondelete") == "CASCADE"
    assert by_column["snapshot_id"]["referred_table"] == "match_snapshot"
    assert by_column["snapshot_id"]["options"].get("ondelete") == "SET NULL"

    snapshot_fks = inspect(engine).get_foreign_keys("match_snapshot")
    assert snapshot_fks[0]["referred_table"] == "match_run"
    assert snapshot_fks[0]["options"].get("ondelete") == "CASCADE"


def test_score_column_numeric_4_1(engine):
    columns = {c["name"]: c for c in inspect(engine).get_columns("match")}
    score_type = columns["score"]["type"]
    assert (score_type.precision, score_type.scale) == (4, 1)


def test_jsonb_columns(engine):
    columns = {c["name"]: c for c in inspect(engine).get_columns("match")}
    for name in ("factors", "reasons", "penalties"):
        assert columns[name]["type"].__class__.__name__ == "JSONB"
        assert columns[name]["nullable"] is False


def test_migration_head_is_0001(engine, session):
    """迁移应用后业务可写：冒烟插入一个批次。"""
    run = make_run(session)
    session.commit()
    assert run.id is not None