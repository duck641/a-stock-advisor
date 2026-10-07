"""板块轮动第二阶段的集中配置。

把阈值集中在这里，便于后续用积累的数据回测调整，避免判断数字散落在代码中。
"""

STATE_ROTATING = "轮动中"
STATE_COOLING = "冷却中"
STATE_WAITING = "待轮动"

# 市场状态决定进入“待轮动”前至少需要冷却的交易日，不再设置冷却上限。
COOLING_MIN_DAYS = {
    "牛市": 3,
    "震荡市": 5,
    "熊市": 10,
}

# 牛市和熊市仍采用热度门槛；震荡市配置只保留为兼容回放数据，实际状态
# 已改由价格形态与量价关系组合直接判断。
ENTRY_RULES = {
    "牛市": {"heat": 70, "strength_percentile": 80},
    "震荡市": {"heat": 75, "strength_percentile": 90},
    "熊市": {"heat": 85, "strength_percentile": 95},
}

# 极强信号仍需满足对应市场的完整进入条件，只是不用等待第二天确认。
HEAT_STRONG_ENTER = 90
HEAT_EXIT = 50
STATE_CONFIRM_DAYS = 2
MIN_DATA_COVERAGE = 0.80
HISTORY_TRADING_DAYS = 90
FETCH_WORKERS = 4
NETWORK_CONNECT_TIMEOUT = 4
NETWORK_READ_TIMEOUT = 8
HISTORY_FETCH_BUDGET_SECONDS = 75

# 震荡市使用最近10日识别价格曲线，最近3日识别当前量价配合。
# 20日窗口只用于判断成交量相对自身历史是放大还是缩小，不参与价格趋势分类。
PATTERN_LOOKBACK_DAYS = 10
RECENT_PATTERN_DAYS = 3
VOLUME_BASELINE_DAYS = 20
TREND_RETURN_THRESHOLD = 3.0
SWING_RETURN_THRESHOLD = 2.0
VOLUME_EXPAND_RATIO = 1.10
VOLUME_EXTREME_HIGH_RATIO = 1.80
VOLUME_EXTREME_LOW_RATIO = 0.60
