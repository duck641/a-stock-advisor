"""美股大盘情绪与板块涨跌工具。"""

from __future__ import annotations

from datetime import datetime

import akshare as ak
import pandas as pd
from langchain_core.tools import tool


US_INDEXES = {
    ".DJI": "道琼斯",
    ".INX": "标普500",
    ".IXIC": "纳斯达克",
}

# 用流动性较好的美股行业 ETF 表示板块表现。A股方向只作外部情绪参考，
# 不表示成分股一一对应，也不直接生成买卖信号。
US_SECTORS = {
    "SOXX": ("半导体", ["芯片", "半导体设备", "算力"]),
    "XLK": ("信息技术", ["软件", "AI", "计算机"]),
    "XLE": ("能源", ["石油", "油服", "煤炭"]),
    "XLB": ("原材料", ["有色金属", "钢铁", "化工"]),
    "XLV": ("医疗", ["创新药", "医疗器械", "CXO"]),
    "XLY": ("可选消费", ["汽车", "家电", "消费电子"]),
    "XLF": ("金融", ["银行", "保险", "券商"]),
    "XLI": ("工业", ["机械", "自动化", "高端制造"]),
}


def _pct_change(close: pd.Series, periods: int) -> float | None:
    if len(close) <= periods:
        return None
    previous = close.iloc[-periods - 1]
    if pd.isna(previous) or previous == 0:
        return None
    return float(round((close.iloc[-1] / previous - 1) * 100, 2))


def _fetch_history(symbol: str) -> pd.DataFrame:
    """获取并整理一个美股指数或 ETF 的日线数据。"""
    frame = ak.index_us_stock_sina(symbol=symbol)
    if frame is None or frame.empty or "close" not in frame:
        raise ValueError(f"{symbol} 没有返回有效行情")

    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result.dropna(subset=["date", "close"]).sort_values("date").tail(60)
    if result.empty:
        raise ValueError(f"{symbol} 的日线数据无法解析")
    return result


def _index_summary(symbol: str, name: str, frame: pd.DataFrame) -> dict:
    close = frame["close"]
    ma5 = float(round(close.tail(5).mean(), 4)) if len(close) >= 5 else None
    ma20 = float(round(close.tail(20).mean(), 4)) if len(close) >= 20 else None
    latest = float(close.iloc[-1])

    if ma5 is not None and ma20 is not None and latest > ma5 > ma20:
        trend = "上涨"
    elif ma5 is not None and ma20 is not None and latest < ma5 < ma20:
        trend = "下跌"
    else:
        trend = "震荡"

    return {
        "指数": name,
        "代码": symbol,
        "数据日期": frame["date"].iloc[-1].strftime("%Y-%m-%d"),
        "最新收盘": round(latest, 4),
        "当日涨跌幅": _pct_change(close, 1),
        "5日涨跌幅": _pct_change(close, 5),
        "20日涨跌幅": _pct_change(close, 20),
        "趋势": trend,
    }


def _sector_summary(symbol: str, frame: pd.DataFrame) -> dict:
    name, a_share_sectors = US_SECTORS[symbol]
    close = frame["close"]
    return {
        "板块": name,
        "代表ETF": symbol,
        "数据日期": frame["date"].iloc[-1].strftime("%Y-%m-%d"),
        "当日涨跌幅": _pct_change(close, 1),
        "5日涨跌幅": _pct_change(close, 5),
        "A股参考方向": a_share_sectors,
    }


def _market_sentiment(indexes: list[dict]) -> tuple[str, list[str]]:
    changes = [item["当日涨跌幅"] for item in indexes if item["当日涨跌幅"] is not None]
    if not changes:
        return "数据不足", ["三大指数没有取得有效涨跌数据"]

    up_count = sum(change > 0 for change in changes)
    down_count = sum(change < 0 for change in changes)
    average = sum(changes) / len(changes)

    if up_count == len(changes) and average >= 1:
        judgement = "明显偏强"
    elif up_count >= 2:
        judgement = "偏强"
    elif down_count == len(changes) and average <= -1:
        judgement = "明显偏弱"
    elif down_count >= 2:
        judgement = "偏弱"
    else:
        judgement = "分化"

    reasons = [
        f"上涨指数 {up_count} 个，下跌指数 {down_count} 个",
        f"三大指数平均当日涨跌幅 {average:.2f}%",
    ]
    return judgement, reasons


@tool
def get_us_market_context() -> dict:
    """查询最近一个美股交易日的大盘情绪和主要板块涨跌。

    当用户分析A股大盘、市场情绪、次日开盘环境、板块强弱或板块推荐，并且
    需要参考隔夜美股表现时调用。用户直接询问美股对A股的影响、美股三大指数、
    美股行业涨跌时也应调用。

    工具包含两个维度：
      1. 道琼斯、标普500、纳斯达克的当日及5/20日表现，用于判断整体风险情绪；
      2. 半导体、科技、能源、原材料、医疗、消费、金融、工业板块的涨跌排名，
         并给出可供A股参考的对应方向。

    不要在只查询单只A股财务或技术指标、且不需要判断大盘和板块环境时调用。
    返回结果是外部市场背景，不能替代A股自身走势，也不能单独作为买卖信号。
    """
    symbols = [*US_INDEXES, *US_SECTORS]
    histories: dict[str, pd.DataFrame] = {}
    errors = []

    # AkShare 的新浪行情解析会初始化 py_mini_racer/V8；该原生库不适合在
    # 多个线程中同时创建实例，因此这里串行请求，避免 V8 初始化时崩溃。
    for symbol in symbols:
        try:
            histories[symbol] = _fetch_history(symbol)
        except Exception as exc:
            errors.append(f"{symbol} 获取失败: {type(exc).__name__}")

    indexes = [
        _index_summary(symbol, name, histories[symbol])
        for symbol, name in US_INDEXES.items()
        if symbol in histories
    ]
    sectors = [
        _sector_summary(symbol, histories[symbol])
        for symbol in US_SECTORS
        if symbol in histories
    ]
    sectors.sort(
        key=lambda item: item["当日涨跌幅"]
        if item["当日涨跌幅"] is not None else float("-inf"),
        reverse=True,
    )

    sentiment, reasons = _market_sentiment(indexes)
    rising = [item for item in sectors if (item["当日涨跌幅"] or 0) > 0][:3]
    falling = sorted(
        (item for item in sectors if (item["当日涨跌幅"] or 0) < 0),
        key=lambda item: item["当日涨跌幅"],
    )[:3]

    dates = [item["数据日期"] for item in [*indexes, *sectors]]
    latest_date = max(dates) if dates else None
    stale = False
    if latest_date:
        stale = (datetime.now().date() - pd.Timestamp(latest_date).date()).days > 4

    if stale:
        data_status = "可能为休市旧数据"
    elif len(set(dates)) > 1:
        data_status = "部分数据日期不一致"
    else:
        data_status = "最近交易日"

    return {
        "数据日期": latest_date,
        "数据状态": data_status,
        "市场情绪": {
            "总体判断": sentiment,
            "判断依据": reasons,
            "三大指数": indexes,
        },
        "板块表现": {
            "领涨板块": rising,
            "领跌板块": falling,
            "全部板块": sectors,
        },
        "A股使用原则": [
            "大盘涨跌只作为A股开盘和短期风险情绪参考",
            "板块涨跌用于观察相关A股方向是否存在外部共振",
            "美股与A股走势冲突时，以A股自身行情、成交量和政策环境为主",
        ],
        "数据错误": errors,
        "数据源": "新浪美股日线（AkShare）",
    }
