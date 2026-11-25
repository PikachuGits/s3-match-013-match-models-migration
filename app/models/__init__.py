"""模型注册入口：导入即把三张交付表挂到 Base.metadata。"""

from app.models.match import Match
from app.models.match_run import MatchRun
from app.models.match_snapshot import MatchSnapshot

__all__ = ["Match", "MatchRun", "MatchSnapshot"]