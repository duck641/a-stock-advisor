"""期货行情查询与增长判断工具。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
from langchain_core.tools import tool


# 常用中文品种名称与新浪主力连续合约代码的对应关系。
# 集中维护映射可以让用户直接说“黄金”或“螺纹钢”，不必记忆合约代码。
FUTURES_ALIASES = {
    "沪深300": "IF0",
    "上证50": "IH0",
    "中证500": "IC0",
    "中证1000": "IM0",
    "黄金": "AU0",
    "白银": "AG0",
    "铜": "CU0",
    "铝": "AL0",
    "原油": "SC0",
    "螺纹钢": "RB0",
    "铁矿石": "I0",
    "焦煤": "JM0",
    "焦炭": "J0",
    "动力煤": "ZC0",
    "生猪": "LH0",
    "豆粕": "M0",
    "玉米": "C0",
    "天然橡胶": "RU0",
    "PTA": "TA0",
    "甲醇": "MA0",
    "玻璃": "FG0",
    "纯碱": "SA0",
}

CONTRACT_NAMES = {code: name for name, code in FUTURES_ALIASES.items()}


def _normalise_symbol(symbol: str) -> str:
    """把中文名称、品种代码或合约代码统一成新浪期货代码。"""
    raw = str(symbol or "").strip()
    if not raw:
        raise ValueError("请提供期货品种或合约代码")

    code = FUTURES_ALIASES.get(raw, raw.upper())
    if code in CONTRACT_NAMES:
        return code

    # 用户输入 AU、IF 等品种代码时，默认查询主力连续合约 AU0、IF0。
    if re.fullmatch(r"[A-Z]{1,3}", code):
        code += "0"
    if not re.fullmatch(r"[A-Z]{1,3}(?:0|\d{3,4})", code):
        raise ValueError(f"无法识别的期货合约代码: {symbol}")
    return code


def _finite_or_none(value) -> float | None:
    """将 Pandas/NumPy 数值转成可安全进行 JSON 序列化的 Python float。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if math.isfinite(number) else None


def _pct_change(series: pd.Series, periods: int) -> float | None:
    """计算指定交易日跨度的涨跌幅，历史不足时返回 None。"""
    if len(series) <= periods:
        return None
    previous = series.iloc[-periods - 1]
    if pd.isna(previous) or previous == 0:
        return None
    return float(round((series.iloc[-1] / previous - 1) * 100, 2))


def _growth_label(change: float | None) -> str:
    """把涨跌幅转换成直观的增长描述，不把小幅波动夸大为强趋势。"""
    if change is None:
        return "数据不足"
    if change >= 5:
        return "明显增长"
    if change > 0:
        return "小幅增长"
    if change <= -5:
        return "明显下降"
    if change < 0:
        return "小幅下降"
    return "基本持平"


def _trend_label(latest: float, ma5: float | None, ma20: float | None) -> str:
    """通过价格与短、中期均线的排列判断总体趋势。"""
    if ma5 is None or ma20 is None:
        return "数据不足"
    if latest > ma5 > ma20:
        return "上涨"
    if latest < ma5 < ma20:
        return "下跌"
    return "震荡"


def _position_judgement(
    price_change_5d: float | None,
    position_change_5d: float | None,
) -> str:
    """结合价格和持仓变化判断资金对当前方向的确认程度。"""
    if price_change_5d is None or position_change_5d is None:
        return "持仓数据不足，暂时无法判断资金方向"
    if price_change_5d > 0 and position_change_5d > 0:
        return "价格上涨且持仓增加，上涨得到新增资金确认"
    if price_change_5d > 0 and position_change_5d < 0:
        return "价格上涨但持仓减少，上涨持续性存疑"
    if price_change_5d < 0 and position_change_5d > 0:
        return "价格下降且持仓增加，空头力量可能增强"
    if price_change_5d < 0 and position_change_5d < 0:
        return "价格下降且持仓减少，可能存在空头减仓"
    return "价格或持仓变化不明显，资金方向暂不明确"


def _standardise_history(raw: pd.DataFrame, days: int) -> pd.DataFrame:
    """将 AkShare 不同版本的期货日线列统一成内部字段。"""
    if raw is None or raw.empty or len(raw.columns) < 5:
        raise ValueError("期货接口没有返回有效日线数据")

    # 新浪接口列名可能随 AkShare 版本变化，但列顺序固定为日期、开高低收、
    # 成交量、持仓量和结算价，因此按接口顺序标准化更稳定。
    frame = raw.iloc[:, : min(len(raw.columns), 8)].copy()
    columns = [
        "date", "open", "high", "low", "close", "volume",
        "open_interest", "settlement",
    ]
    frame.columns = columns[: len(frame.columns)]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in columns[1:]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna(subset=["date", "close"]).sort_values("date").tail(days)
    if frame.empty:
        raise ValueError("期货日线数据无法解析")
    return frame


@tool
def get_futures_market(symbol: str, days: int = 60) -> dict:
    """查询期货行情、关键指标，并判断短期和中期是否增长。

    什么时候应该调用：
      1. 用户直接询问期货价格、走势、涨跌、增长或关键指标时。
      2. 用户询问与商品期货明显相关的股票或板块时，例如黄金、有色、石油、
         钢铁、煤炭、养殖、饲料、航空、玻璃和纯碱等方向。
      3. 模型准备推荐或返回上述期货相关股票、板块时，应在最终回答前调用，
         用真实期货数据检查相关商品当前是增长、下降还是震荡。
      4. 发生可能影响商品供需或价格的事件时，例如减产、矿山停产、地缘冲突、
         极端天气、库存变化或产业政策，应查询受影响的期货品种。

    常见关联示例：黄金股看 AU，石油和航空看 SC，钢铁看 RB/I/JM，
    生猪养殖和饲料看 LH/M/C，有色金属看 CU/AL，大盘指数看 IF/IH/IC/IM。

    什么情况下不应调用：
      1. 用户询问的股票、板块或事件与期货价格没有明确关系。
      2. 用户询问期权、外汇、基金或加密货币，本工具不提供这些市场的数据。
    品种选择：用户未直接给出品种，但股票、板块或事件与某个品种有明确关联时，
    应根据关联选择品种并查询；只有上下文不足以确定品种时才先询问用户。

    参数：
      symbol: 期货中文名称、品种代码或具体合约代码。
              中文示例：黄金、原油、螺纹钢、沪深300。
              品种代码示例：AU、SC、RB、IF，自动查询对应主力连续合约。
              合约代码示例：AU0、IF0、RB2410。
      days: 用于计算指标的最近交易日数量，范围 25-250，默认 60。

    返回：
      - 基本信息：期货品种、合约代码、数据日期、最新价格；
      - 关键指标：5/10/20日涨跌幅，MA5/MA10/MA20，成交量和持仓量；
      - 增长判断：短期、中期、总体趋势和资金持仓判断；
      - 数据源及有效交易日数量。

    注意：该工具只判断期货自身的行情和增长趋势，不直接生成股票买卖建议。
    """
    code = _normalise_symbol(symbol)
    if not 25 <= days <= 250:
        raise ValueError("days 必须在 25 到 250 之间")

    end = datetime.now()
    start = end - timedelta(days=max(days * 3, 75))
    raw = ak.futures_main_sina(
        symbol=code,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    frame = _standardise_history(raw, days)

    close = frame["close"]
    latest = float(close.iloc[-1])
    change_5d = _pct_change(close, 5)
    change_10d = _pct_change(close, 10)
    change_20d = _pct_change(close, 20)
    ma5 = _finite_or_none(close.tail(5).mean()) if len(close) >= 5 else None
    ma10 = _finite_or_none(close.tail(10).mean()) if len(close) >= 10 else None
    ma20 = _finite_or_none(close.tail(20).mean()) if len(close) >= 20 else None

    open_interest = None
    position_change_5d = None
    if "open_interest" in frame and frame["open_interest"].notna().any():
        positions = frame["open_interest"].dropna()
        open_interest = _finite_or_none(positions.iloc[-1])
        position_change_5d = _pct_change(positions, 5)

    trend = _trend_label(latest, ma5, ma20)
    reasons = [
        f"5日涨跌幅为 {change_5d}%" if change_5d is not None else "5日数据不足",
        f"20日涨跌幅为 {change_20d}%" if change_20d is not None else "20日数据不足",
        f"最新价与均线排列显示为{trend}趋势",
    ]

    return {
        "期货品种": CONTRACT_NAMES.get(code, code.rstrip("0123456789")),
        "合约代码": code,
        "数据日期": frame["date"].iloc[-1].strftime("%Y-%m-%d"),
        "最新价格": _finite_or_none(latest),
        "关键指标": {
            "5日涨跌幅": change_5d,
            "10日涨跌幅": change_10d,
            "20日涨跌幅": change_20d,
            "MA5": ma5,
            "MA10": ma10,
            "MA20": ma20,
            "成交量": _finite_or_none(frame["volume"].iloc[-1])
            if "volume" in frame else None,
            "持仓量": open_interest,
            "持仓量5日变化幅度": position_change_5d,
        },
        "增长判断": {
            "短期": _growth_label(change_5d),
            "中期": _growth_label(change_20d),
            "总体趋势": trend,
            "资金判断": _position_judgement(change_5d, position_change_5d),
            "判断依据": reasons,
        },
        "有效交易日": len(frame),
        "数据源": "新浪期货主力连续日线（AkShare）",
    }
