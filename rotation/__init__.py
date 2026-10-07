"""板块轮动基础模块。

提供市场状态、行业板块三状态和独立数据存储。事件映射与候选评分将在后续
阶段建立，避免把不同职责堆在一个文件中。
"""

from rotation.storage import MarketRegimeStorage, RotationStorage


# 使用延迟导入，既保留简洁调用入口，也避免执行
# ``python -m rotation.sector_state`` 时模块被提前加载两次。
def update_sector_states(*args, **kwargs):
    from rotation.sector_state import update_sector_states as implementation
    return implementation(*args, **kwargs)


def get_sector_state(*args, **kwargs):
    from rotation.sector_state import get_sector_state as implementation
    return implementation(*args, **kwargs)


def list_sector_states(*args, **kwargs):
    from rotation.sector_state import list_sector_states as implementation
    return implementation(*args, **kwargs)


__all__ = [
    "MarketRegimeStorage",
    "RotationStorage",
    "update_sector_states",
    "get_sector_state",
    "list_sector_states",
]
