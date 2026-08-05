"""
单只股票历史行情数据工具 - 供 LangGraph ReAct 智能体调用

数据源：腾讯（日线历史）、新浪（日内分时历史）

依赖声明：
  - akshare.stock_zh_a_hist_tx   — 腾讯日线历史数据
  - akshare.stock_intraday_sina  — 新浪日内分时数据（3秒粒度）
"""

from langchain_core.tools import tool
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
from tools.stock.select_signal_stock import get_stock_info


# ════════════════════════════════════════════════
# 工具1：日线历史行情
# ════════════════════════════════════════════════

@tool
def get_stock_daily_history(
    stock_num: str,
    days: int = 60,
    start_date: str = "",
    end_date: str = "",
) -> pd.DataFrame:
    """
    获取单只股票的历史日线行情数据（腾讯数据源）。

    Dependencies: [complete_stock_info]

    如果不传 start_date 和 end_date，则按 days 参数回溯。
    如果传了 start_date 和 end_date，则忽略 days 参数。

    参数:
        stock_num: 6位股票代码，如 "002479"
        days: 回溯天数，默认60天（仅当不传起止日期时生效）
        start_date: 起始日期，格式 YYYYMMDD，如 "20260701"
        end_date: 截止日期，格式 YYYYMMDD，如 "20260722"

    返回:
        DataFrame包含: date, open, close, high, low, amount(万元)
    """
    formatted_stock = get_stock_info(stock_num)
    if not formatted_stock:
        raise ValueError(f"无法识别的股票代码: {stock_num}")

    if start_date and end_date:
        _start = start_date
        _end = end_date
    else:
        _end = datetime.now().strftime("%Y%m%d")
        _start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    df = ak.stock_zh_a_hist_tx(
        symbol=formatted_stock.upper(),
        start_date=_start,
        end_date=_end,
    )
    return df


# ════════════════════════════════════════════════
# 工具2：历史日内分时数据（指定日期的）
# ════════════════════════════════════════════════

@tool
def get_stock_history_intraday(stock_num: str, date: str) -> pd.DataFrame:
    """
    获取单只股票在指定历史日期的日内分时交易数据（新浪数据源，3秒粒度）。

    Dependencies: [complete_stock_info]

    注意：只能查询最近几个交易日的数据，太久远的历史分时数据无法获取。

    参数:
        stock_num: 6位股票代码，如 "002479"
        date: 8位日期，如 "20260722"

    返回:
        DataFrame包含: symbol, name, ticktime, price, volume, prev_price, kind
        kind字段: U=上涨, D=下跌, E=持平
    """
    if len(stock_num) != 6:
        raise ValueError(f"股票代码必须是6位，当前为{len(stock_num)}位: {stock_num}")
    if len(date) != 8:
        raise ValueError(f"日期必须是8位，当前为{len(date)}位: {date}")

    formatted_stock = get_stock_info(stock_num)
    if not formatted_stock:
        raise ValueError(f"无法识别的股票代码: {stock_num}")

    df = ak.stock_intraday_sina(symbol=formatted_stock, date=date)
    return df
