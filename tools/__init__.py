"""
股票分析工具包 - 二级目录组织结构

分类：
  tools/stock/     — 个股数据（分时、实时、日线、基本面、技术面）
  tools/market/    — 市场环境（行业、情绪、大盘）
  tools/signal/    — 买卖信号
  tools/info/      — 信息补全（代码⇄名称）
  tools/core/      — 核心调度（并发、错误处理、工具管理）
"""

from tools.stock import (
    get_stock_info,
    select_signal_stock,
    get_realtime_quote,
    get_fundamental_info,
    get_technical_info,
    get_stock_daily_history,
    get_stock_history_intraday,
    get_dragon_tiger_list,
)

from tools.market import (
    get_market_sentiment,
    get_market_environment,
)

from tools.stock_board import (
    get_sector_rankings,
    get_sector_leaders,
    get_sector_history,
)

from tools.invest_kalendar import (
    get_investment_calendar,
)

from tools.signal import (
    generate_trade_signal,
)

from tools.info import (
    resolve_stock,
    complete_stock_info,
)

from tools.core import (
    concurrent_tool_node,
    execute_with_error_handling,
    classify_error,
    format_error_for_llm,
    ErrorCategory,
    ToolError,
    list_tools,
    show_tool_help,
    quick_call,
    summary,
    load_skill,
    list_skills,
)

# 所有工具的列表，供 LangGraph 绑定
TOOLS = [
    # 个股信息类
    select_signal_stock,
    get_realtime_quote,
    # 基本面
    get_fundamental_info,
    # 技术面
    get_technical_info,
    # 历史行情类
    get_stock_daily_history,
    get_stock_history_intraday,
    # 市场环境类
    get_market_sentiment,
    get_market_environment,
    # 板块历史走势
    get_sector_history,
    # 板块成分股
    get_sector_rankings,
    get_sector_leaders,
    # 买卖信号
    generate_trade_signal,
    # 信息补全
    complete_stock_info,
    # 技能管理
    load_skill,
    list_skills,
    # 龙虎榜
    get_dragon_tiger_list,
    # 投资日历
    get_investment_calendar,
]
