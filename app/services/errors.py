"""服务层统一异常。"""

from __future__ import annotations


class MatchValidationError(ValueError):
    """入参或状态不满足工单约定（字典、范围、状态机、唯一性等）。"""