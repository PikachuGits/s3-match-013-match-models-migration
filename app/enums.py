"""字典常量：与数据库 CHECK 约束一一对应，服务层复用同一份字典做前置校验。

采用 VARCHAR + CHECK（而非 PG ENUM），保证迁移 downgrade 可逆且字典可演进。
"""

from __future__ import annotations

# match_run.trigger
RUN_TRIGGERS: tuple[str, ...] = ("publish", "manual", "nightly")

# match_run.status
RUN_STATUSES: tuple[str, ...] = ("running", "done", "failed")
RUN_TERMINAL_STATUSES: tuple[str, ...] = ("done", "failed")

# match.target_type
TARGET_TYPES: tuple[str, ...] = ("company", "supply_item")

# match.status
MATCH_STATUSES: tuple[str, ...] = (
    "pending_review",
    "adopted",
    "rejected",
    "later",
    "expired",
)
# 允许发起复核动作的状态；adopted / rejected / expired 为终态
REVIEWABLE_STATUSES: tuple[str, ...] = ("pending_review", "later")

# match.reject_reason 字典（工单原文）
REJECT_REASONS: tuple[str, ...] = ("不对口", "时机不对", "已有合作", "其他")

# 复核动作 → 目标状态
REVIEW_ACTIONS: dict[str, str] = {
    "adopt": "adopted",
    "reject": "rejected",
    "later": "later",
}

# pending_review 超期天数（超过 14 天置为 expired）
PENDING_REVIEW_TTL_DAYS = 14