# S3-MATCH-013 · 匹配推荐 match 与匹配批次模型及迁移

会员空间平台「供需、匹配与商机」模块的数据模型交付：一次匹配批次（`match_run`）、
批次内的匹配推荐（`match`）、打分输入特征快照（`match_snapshot`），外加
「pending_review 超过 14 天未复核自动过期」的定时任务。

- 技术栈：Python 3.11+ / SQLAlchemy 2 / Alembic / PostgreSQL 16（实测 3.12.12 + PG 16.16）
- 测试：**159 passed**（真实 PostgreSQL，见 `evidence/test-results.txt`）
- 迁移：upgrade → downgrade → upgrade 往返成功，`alembic check` 零漂移（见 `evidence/migration-check.txt`）

## 快速开始

```bash
# 1) 独立虚拟环境
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2) PostgreSQL 16 测试库（库名必须含 test：测试会重建 public schema）
createdb -h 127.0.0.1 -p 5432 clawhunt_2010_test

# 3) 配置只从环境变量读取
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/clawhunt_2010_test'
export TEST_DATABASE_URL="$DATABASE_URL"

# 4) 应用迁移
.venv/bin/alembic upgrade head

# 5) 全量测试 / 迁移往返验证
./scripts/test.sh                                   # 可加 EVIDENCE_OUT=evidence/test-results.txt
DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/SCRATCH_DB' \
  ./scripts/migration-check.sh
```

## 表结构

三张表均含平台统一业务列：`id(UUID)`、`org_id`、`site_id`、`created_at`、`created_by`、
`updated_at`、`deleted_at`（软删除）。字典一律 **VARCHAR + 命名 CHECK**（不用 PG ENUM），
保证 downgrade 可逆、字典可演进。

### match_run（匹配批次）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| source_item_id | UUID | 触发匹配的供需条目（跨工单引用，见对接点） |
| trigger | VARCHAR(16) | `publish` / `manual` / `nightly` |
| algo_version | VARCHAR(64) | 匹配算法版本 |
| weight_version_id | UUID? | 权重版本（跨工单引用） |
| status | VARCHAR(16) | `running` / `done` / `failed`，默认 `running` |
| candidate_count | INTEGER | 候选数，CHECK ≥ 0，默认 0 |
| started_at / finished_at | timestamptz | CHECK `finished_at ≥ started_at` |

### match（匹配推荐）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| run_id | UUID FK | → match_run，ON DELETE CASCADE |
| source_item_id | UUID | 与批次一致（服务层保证） |
| target_type | VARCHAR(16) | `company` / `supply_item` |
| target_id | UUID | 目标 id |
| target_company_id | UUID? | 目标所属公司 |
| score | NUMERIC(4,1) | 0–100，一位小数（范围 CHECK + 固定标度） |
| rank | INTEGER | CHECK ≥ 1 |
| factors / reasons / penalties | JSONB | 打分明细，结构见下 |
| snapshot_id | UUID? FK | → match_snapshot，ON DELETE SET NULL |
| status | VARCHAR(16) | `pending_review` / `adopted` / `rejected` / `later` / `expired` |
| reviewer_id / reviewed_at | UUID? / timestamptz | 已复核状态必须二者齐全（CHECK） |
| reject_reason | VARCHAR(16)? | 字典：`不对口` / `时机不对` / `已有合作` / `其他`（CHECK） |
| reject_note | TEXT? | 拒绝备注 |
| opportunity_id | UUID? | 采纳后关联的商机（跨工单引用） |

约束与索引：

- **`uq_match_run_target (run_id, target_type, target_id)` 唯一**（验收标准 3）
- `rejected` 必须携带字典内 `reject_reason`（`ck_match_rejected_reason`）
- `ix_match_status_created (status, created_at)` 支撑过期扫描；另有 source_item /
  target_company / opportunity / org+site+status 索引

### match_snapshot（打分输入特征）

一个批次一份（`run_id` 唯一），固化 `source_item_id` / `algo_version` /
`weight_version_id` 与 `features(JSONB)`。`features` 建议结构：

```json
{
  "source_item": {"item_id": "…", "kind": "demand", "industry": "…"},
  "candidates": [{"company_id": "…", "name": "…"}],
  "weights": {"industry_fit": 0.5, "region_fit": 0.3, "history_fit": 0.2}
}
```

### factors / reasons / penalties JSON 结构

```json
{
  "factors": [
    {"name": "industry_fit", "raw": 12.0, "normalized": 0.8, "weight": 0.5, "contribution": 40.0}
  ],
  "reasons": ["行业对口", "同城"],
  "penalties": [{"code": "already_partner", "points": 10.0}]
}
```

服务层校验：`normalized` / `weight` ∈ [0,1]，`raw` / `contribution` 为数值，
`penalties.points` 为非负扣分量；`reasons` 为非空字符串数组。

## 服务层与状态机

`app/services/`：`create_run` / `finish_run`、`create_snapshot`、
`add_match` / `add_matches`、`review_match`。校验失败统一抛 `MatchValidationError`，
数据库约束兜底（两层都有测试）。

复核状态机：`pending_review` / `later` → `adopted` / `rejected` / `later`；
`adopted` / `rejected` / `expired` 为终态；`rejected` 必须给字典内原因；
仅 `adopted` 可关联 `opportunity_id`。`score` 超过一位小数在服务层直接拒绝
（数据库 NUMERIC(4,1) 会舍入，见测试对两种行为的分别断言）。

## 过期任务（验收标准 4）

`app/tasks/expire.py::expire_pending_matches(session, now=None, ttl=14天)`：

- `now` 可注入 → 冻结时间测试 / 回放；默认当前 UTC
- 边界为**严格超过**：`created_at < now - 14天` 才过期，恰好 14 天不动
- 只处理未软删除且仍为 `pending_review` 的行；批量 UPDATE + RETURNING，幂等
- 返回本次过期的 match id 列表，调用方提交事务

CLI（供 cron / 调度器周期调用，如每 30 分钟）：

```bash
DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/DB' \
  .venv/bin/python scripts/expire_matches.py            # 实际执行
  # 可选：--now 2026-11-25T12:00:00+00:00 --ttl-days 14 --dry-run
# crontab 示例：
# */30 * * * * cd /path/to/repo && DATABASE_URL=… .venv/bin/python scripts/expire_matches.py >> expire.log 2>&1
```

输出 JSON：`cutoff / expired_count / match_ids / dry_run`。

## 对接点（依赖工单以接口桩先行）

本单不建跨工单外键，以下均为 UUID 引用，集成时由对应工单的表接管：

| 字段 | 对接工单域 | 说明 |
| --- | --- | --- |
| match_run.source_item_id / match.source_item_id | 供需条目（supply/demand item） | 触发匹配的条目 |
| match_run.weight_version_id / match_snapshot.weight_version_id | 匹配权重配置 | 权重版本 |
| match.opportunity_id | 撮合商机（opportunity） | 采纳推荐后转商机 |
| match.reviewer_id / created_by | 用户与权限 | 复核人 / 创建人 |
| org_id / site_id | 组织与站点主数据 | 租户隔离 |

## 验收标准对照

1. 迁移 upgrade / downgrade 均成功 → `tests/test_migration.py`（含临时库往返、主库往返、零漂移）
2. reject_reason 不在字典内保存失败 → `tests/test_constraints.py`（DB CHECK）+ `tests/test_services.py`（服务层）
3. 同一批次同一目标重复写入被唯一约束拒绝 → `tests/test_constraints.py::test_duplicate_target_in_same_run_rejected`
4. 冻结时间：14 天未复核 → expired → `tests/test_expire_task.py`（14 天边界、终态/软删除不动、幂等、CLI）

## 合规

- 全部测试数据为虚构（虚构企业甲 / 虚构行业A 等），不含真实个人 / 企业信息，不出现客户名称
- 配置与密钥只从环境变量读取（`DATABASE_URL` / `TEST_DATABASE_URL`），仓库不落凭据
- 不引入任何向境外发送数据的第三方 SDK；除 PostgreSQL 外无出站请求
- 本单信息仅用于完成本单

## 目录结构

```
app/
  db.py                 # Base / 引擎 / session_scope（URL 只读环境变量）
  enums.py              # 字典常量（与 CHECK 一一对应）
  models/               # match_run / match / match_snapshot + 统一业务列
  services/             # 批次 / 快照 / 推荐写入与复核
  tasks/expire.py       # 14 天过期任务
migrations/versions/0001_match_models.py
scripts/                # test.sh / migration-check.sh / expire_matches.py
tests/                  # 159 项，真实 PostgreSQL 16
evidence/               # 测试输出 / 迁移往返 / 环境版本
```