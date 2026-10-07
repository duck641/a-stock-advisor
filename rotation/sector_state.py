"""行业板块三状态识别与冷却期管理。

板块只有三个业务状态：轮动中、冷却中、待轮动。牛市和熊市保留热度规则，
震荡市由10日价格形态与逐日量价关系的组合直接判断；事件和个股选择不属于本模块。
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from rotation.config import (
    COOLING_MIN_DAYS,
    ENTRY_RULES,
    FETCH_WORKERS,
    HEAT_EXIT,
    HEAT_STRONG_ENTER,
    HISTORY_FETCH_BUDGET_SECONDS,
    HISTORY_TRADING_DAYS,
    MIN_DATA_COVERAGE,
    NETWORK_CONNECT_TIMEOUT,
    NETWORK_READ_TIMEOUT,
    PATTERN_LOOKBACK_DAYS,
    RECENT_PATTERN_DAYS,
    STATE_CONFIRM_DAYS,
    STATE_COOLING,
    STATE_ROTATING,
    STATE_WAITING,
    SWING_RETURN_THRESHOLD,
    TREND_RETURN_THRESHOLD,
    VOLUME_BASELINE_DAYS,
    VOLUME_EXPAND_RATIO,
    VOLUME_EXTREME_HIGH_RATIO,
    VOLUME_EXTREME_LOW_RATIO,
)
from rotation.storage import RotationStorage


logger = logging.getLogger(__name__)
SOURCE_NAME = "同花顺行业板块（AkShare）"


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _prepare_sector_history(frame: pd.DataFrame) -> pd.DataFrame:
    """把同花顺中文字段转换为模块内部统一字段。"""
    if frame is None or frame.empty or "日期" not in frame.columns or "收盘价" not in frame.columns:
        raise ValueError("板块历史行情缺少日期或收盘价")

    result = pd.DataFrame({
        "trade_date": pd.to_datetime(frame["日期"], errors="coerce"),
        "open": _numeric(frame, "开盘价"),
        "high": _numeric(frame, "最高价"),
        "low": _numeric(frame, "最低价"),
        "close": _numeric(frame, "收盘价"),
        "volume": _numeric(frame, "成交量"),
        "amount": _numeric(frame, "成交额"),
    })
    result = (
        result.dropna(subset=["trade_date", "close"])
        .sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
    )
    if len(result) < 25:
        raise ValueError(f"有效行情不足25个交易日，当前只有 {len(result)} 条")
    return result


def _prepare_benchmark(frame: pd.DataFrame) -> pd.DataFrame:
    """整理沪深300行情，供板块计算相对收益。"""
    if frame is None or frame.empty:
        raise ValueError("沪深300历史行情为空")
    date_column = "date" if "date" in frame.columns else "日期"
    close_column = "close" if "close" in frame.columns else "收盘"
    if date_column not in frame.columns or close_column not in frame.columns:
        raise ValueError("沪深300行情缺少日期或收盘列")
    result = pd.DataFrame({
        "trade_date": pd.to_datetime(frame[date_column], errors="coerce"),
        "benchmark_close": pd.to_numeric(frame[close_column], errors="coerce"),
    })
    result = result.dropna().sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    for days in (3, 5, 10):
        result[f"benchmark_return_{days}d"] = result["benchmark_close"].pct_change(days) * 100
    return result


def _add_metrics(frame: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    """计算单个板块的收益、趋势、量能和回撤。"""
    result = frame.copy()
    close = result["close"]
    result["return_1d"] = close.pct_change() * 100
    for days in (3, 5, 10, 20):
        result[f"return_{days}d"] = close.pct_change(days) * 100
    for days in (5, 10, 20):
        result[f"ma{days}"] = close.rolling(days).mean()

    volume_ma20 = result["volume"].rolling(20).mean()
    result["volume_ratio_20d"] = result["volume"] / volume_ma20.replace(0, pd.NA)
    high_20 = result["high"].rolling(20).max()
    result["drawdown_20d_high"] = (close / high_20 - 1) * 100
    # 当日低点等于最近5日最低点时视为创新低；后续稳定判断要求连续两天不创新低。
    result["new_5d_low"] = result["low"] <= result["low"].rolling(5).min()

    result = result.merge(benchmark, on="trade_date", how="left")
    for days in (3, 5, 10):
        result[f"relative_{days}d"] = (
            result[f"return_{days}d"] - result[f"benchmark_return_{days}d"]
        )
    return result


def _heat_score(row: pd.Series) -> int:
    """把相对强度、涨幅、成交量和均线压缩为0到100的轮动热度。"""
    percentile_value = row.get("strength_percentile")
    percentile = float(percentile_value) if pd.notna(percentile_value) else 0.0
    relative_points = max(0.0, min(40.0, (percentile - 50) / 50 * 40))

    return_points = 0
    return_3d = row.get("return_3d")
    return_5d = row.get("return_5d")
    if pd.notna(return_3d) and return_3d > 0:
        return_points += 10
    if pd.notna(return_5d) and return_5d > 0:
        return_points += 10
    if pd.notna(return_5d) and return_5d >= 3:
        return_points += 5

    ratio = row.get("volume_ratio_20d")
    volume_points = 0
    if pd.notna(ratio):
        if ratio >= 1.2:
            volume_points = 20
        elif ratio >= 1.0:
            volume_points = 12
        elif ratio >= 0.8:
            volume_points = 5

    trend_points = 0
    ma5 = row.get("ma5")
    ma10 = row.get("ma10")
    ma20 = row.get("ma20")
    if pd.notna(ma5) and row["close"] > ma5:
        trend_points += 5
    if pd.notna(ma5) and pd.notna(ma10) and ma5 > ma10:
        trend_points += 5
    if pd.notna(ma10) and pd.notna(ma20) and ma10 > ma20:
        trend_points += 5
    return int(round(max(0, min(100, relative_points + return_points + volume_points + trend_points))))


def _is_entry_candidate(row: pd.Series, market_regime: str) -> bool:
    """按市场环境判断轮动候选，避免普通反弹被识别为板块轮动。"""
    rule = ENTRY_RULES.get(market_regime, ENTRY_RULES["震荡市"])
    own_positive = row.get("return_3d", 0) > 0 or row.get("return_5d", 0) > 0
    relative_positive = row.get("relative_3d", 0) > 0 or row.get("relative_5d", 0) > 0
    leading_sector = row.get("strength_percentile", 0) >= rule["strength_percentile"]
    return (
        row["rotation_heat"] >= rule["heat"]
        and leading_sector
        and own_positive
        and relative_positive
    )


def _window_return(start: float, end: float) -> float:
    """返回区间百分比收益，避免异常零价格参与形态判断。"""
    return ((end / start) - 1) * 100 if start else 0.0


def _classify_price_pattern(rows: pd.DataFrame, position: int) -> tuple[str, list[str]]:
    """识别最近10日价格曲线；特殊转折形态优先于普通涨跌趋势。"""
    start = position - PATTERN_LOOKBACK_DAYS + 1
    if start < 0:
        return "形态数据不足", [f"价格历史不足{PATTERN_LOOKBACK_DAYS}个交易日"]

    window = rows.iloc[start:position + 1].reset_index(drop=True)
    close = pd.to_numeric(window["close"], errors="coerce")
    low = pd.to_numeric(window["low"], errors="coerce")
    if close.isna().any() or low.isna().any():
        return "形态数据不足", ["价格数据存在缺失"]

    daily = close.pct_change(fill_method=None).mul(100).dropna().reset_index(drop=True)
    total_return = _window_return(float(close.iloc[0]), float(close.iloc[-1]))
    background_return = _window_return(float(close.iloc[0]), float(close.iloc[6]))
    recent = daily.tail(RECENT_PATTERN_DAYS).tolist()
    recent_return = _window_return(float(close.iloc[-4]), float(close.iloc[-1]))
    # 完全横盘时标准差为零，直接按无趋势处理，避免相关系数产生运行时警告。
    if close.nunique() <= 1:
        trend_correlation = 0.0
    else:
        correlation = pd.Series(range(len(close)), dtype="float64").corr(close)
        trend_correlation = float(correlation) if pd.notna(correlation) else 0.0

    # “上涨后回落收窄”要求先涨、后跌，并且最后几日跌幅减小或已经温和修复。
    had_pullback = min(recent) <= -0.5 or recent_return <= -1.0
    decline_narrowing = (
        recent[0] < 0
        and recent[1] < 0
        and recent[1] > recent[0]
        and (recent[2] >= 0 or recent[2] > recent[1])
    )
    slow_repair = recent_return < 0 and recent[-1] > 0
    recent_low_stable = float(low.iloc[-1]) >= float(low.iloc[-3:-1].min())
    if (
        background_return >= SWING_RETURN_THRESHOLD
        and had_pullback
        and (decline_narrowing or slow_repair)
    ):
        reasons = [
            f"前7日上涨 {background_return:+.2f}%",
            f"近3日变动 {recent_return:+.2f}%",
            "近期跌幅收窄或出现缓慢修复",
        ]
        if recent_low_stable:
            reasons.append("最新低点没有继续下移")
        return "上涨后回落收窄", reasons

    trough = int(close.idxmin())
    if 2 <= trough <= 7:
        fall = _window_return(float(close.iloc[0]), float(close.iloc[trough]))
        recovery = _window_return(float(close.iloc[trough]), float(close.iloc[-1]))
        if fall <= -SWING_RETURN_THRESHOLD and recovery >= SWING_RETURN_THRESHOLD:
            return "V形反转", [
                f"低点前回落 {fall:+.2f}%",
                f"低点后修复 {recovery:+.2f}%",
            ]

    peak = int(close.idxmax())
    if 2 <= peak <= 7:
        rise = _window_return(float(close.iloc[0]), float(close.iloc[peak]))
        retreat = _window_return(float(close.iloc[peak]), float(close.iloc[-1]))
        if rise >= SWING_RETURN_THRESHOLD and retreat <= -SWING_RETURN_THRESHOLD:
            return "倒V形", [
                f"高点前上涨 {rise:+.2f}%",
                f"高点后回落 {retreat:+.2f}%",
            ]

    up_days = int((daily > 0).sum())
    down_days = int((daily < 0).sum())
    if total_return >= TREND_RETURN_THRESHOLD and up_days >= 6 and trend_correlation >= 0.65:
        return "单边上涨", [
            f"10日上涨 {total_return:+.2f}%",
            f"上涨日 {up_days} 天，价格重心持续抬高",
        ]
    if total_return <= -TREND_RETURN_THRESHOLD and down_days >= 6 and trend_correlation <= -0.65:
        return "单边下跌", [
            f"10日下跌 {total_return:+.2f}%",
            f"下跌日 {down_days} 天，价格重心持续下移",
        ]
    return "震荡整理", [
        f"10日变动 {total_return:+.2f}%",
        "10日内没有形成明确单边趋势或转折形态",
    ]


def _classify_volume_price(rows: pd.DataFrame, position: int) -> tuple[str, list[str]]:
    """逐日匹配涨跌和成交量，避免三日均量掩盖某一天的异常放量。"""
    if position < RECENT_PATTERN_DAYS:
        return "量价数据不足", ["缺少近期量价对照数据"]

    recent = rows.iloc[position - RECENT_PATTERN_DAYS + 1:position + 1].copy()
    price_segment = rows.iloc[position - RECENT_PATTERN_DAYS:position + 1]
    recent_returns = price_segment["close"].pct_change(fill_method=None).mul(100).tail(
        RECENT_PATTERN_DAYS
    )
    recent_volumes = pd.to_numeric(recent["volume"], errors="coerce")
    baseline_start = max(0, position - VOLUME_BASELINE_DAYS)
    baseline_end = position - RECENT_PATTERN_DAYS + 1
    baseline = pd.to_numeric(
        rows.iloc[baseline_start:baseline_end]["volume"], errors="coerce"
    ).dropna()
    if recent_volumes.isna().any() or baseline.empty or float(baseline.median()) <= 0:
        return "量价数据不足", ["成交量基准不足"]

    baseline_volume = float(baseline.median())
    volume_ratios = recent_volumes / baseline_volume
    ten_day = rows.iloc[max(0, position - PATTERN_LOOKBACK_DAYS + 1):position + 1]
    price_low = float(ten_day["low"].min())
    price_high = float(ten_day["high"].max())
    current_close = float(rows.iloc[position]["close"])
    price_position = (
        (current_close - price_low) / (price_high - price_low)
        if price_high > price_low else 0.5
    )

    # 天量、地量同时要求价格处在相应位置；单看成交量极值没有方向意义。
    max_offset = int(volume_ratios.reset_index(drop=True).idxmax())
    extreme_row = recent.iloc[max_offset]
    extreme_range = float(extreme_row["high"] - extreme_row["low"])
    close_strength = (
        float(extreme_row["close"] - extreme_row["low"]) / extreme_range
        if extreme_range > 0 else 0.5
    )
    if float(volume_ratios.max()) >= VOLUME_EXTREME_HIGH_RATIO and price_position >= 0.80:
        strong = float(recent_returns.iloc[max_offset]) > 0 and close_strength >= 0.70
        label = "天量天价（收盘强）" if strong else "天量天价（冲高回落）"
        return label, [
            f"近期最大量为基准量的 {float(volume_ratios.max()):.2f} 倍",
            f"价格位于10日区间的 {price_position:.0%}",
            f"天量日收盘强度 {close_strength:.0%}",
        ]
    if float(volume_ratios.mean()) <= VOLUME_EXTREME_LOW_RATIO and price_position <= 0.25:
        return "地量低价", [
            f"近3日均量为基准量的 {float(volume_ratios.mean()):.2f} 倍",
            f"价格位于10日区间的 {price_position:.0%}",
        ]

    recent_return = float(recent_returns.sum())
    up_mask = recent_returns.reset_index(drop=True) > 0
    down_mask = recent_returns.reset_index(drop=True) < 0
    aligned_volumes = recent_volumes.reset_index(drop=True)
    up_volume = float(aligned_volumes[up_mask].mean()) if up_mask.any() else 0.0
    down_volume = float(aligned_volumes[down_mask].mean()) if down_mask.any() else 0.0

    if recent_return > 0:
        expanded = (
            up_volume >= baseline_volume * VOLUME_EXPAND_RATIO
            and (down_volume == 0 or up_volume >= down_volume)
        )
        label = "放量上涨" if expanded else "缩量上涨"
    elif recent_return < 0:
        expanded = (
            down_volume >= baseline_volume * VOLUME_EXPAND_RATIO
            and (up_volume == 0 or down_volume >= up_volume)
        )
        label = "放量下跌" if expanded else "缩量下跌"
    else:
        label = "量价平稳"
    return label, [
        f"近3日累计变动 {recent_return:+.2f}%",
        f"近3日均量为历史中位量的 {float(volume_ratios.mean()):.2f} 倍",
        "上涨日与下跌日成交量已分别比较",
    ]


def _sideways_combination_state(price_pattern: str, volume_pattern: str) -> tuple[str, str]:
    """震荡市必须同时命中价格形态和量价关系，并在当日直接得到状态。"""
    bullish_volume = {"放量上涨", "缩量上涨", "天量天价（收盘强）"}
    bearish_volume = {"放量下跌", "天量天价（冲高回落）"}

    if price_pattern == "单边上涨":
        if volume_pattern in bullish_volume:
            return STATE_ROTATING, "单边上涨得到上涨量价关系确认"
        return STATE_COOLING, "上涨曲线没有得到健康量价关系确认"
    if price_pattern == "上涨后回落收窄":
        if volume_pattern in {"缩量下跌", "缩量上涨", "放量上涨"}:
            return STATE_WAITING, "上涨后的回落正在收窄或修复"
        return STATE_COOLING, "回落阶段仍有明显抛压"
    if price_pattern == "V形反转":
        if volume_pattern in bullish_volume:
            return STATE_WAITING, "V形修复得到上涨量价关系确认"
        return STATE_COOLING, "V形曲线尚未得到修复量能确认"
    if price_pattern == "震荡整理":
        if volume_pattern in {"放量上涨", "天量天价（收盘强）"}:
            return STATE_WAITING, "震荡整理后出现主动上涨量能"
        return STATE_COOLING, "震荡整理尚未出现有效启动信号"
    if price_pattern in {"单边下跌", "倒V形"}:
        return STATE_COOLING, "下跌结构没有完成企稳修复"
    if volume_pattern in bearish_volume:
        return STATE_COOLING, "价格形态不完整且下跌日量能偏强"
    return STATE_COOLING, "价格或量价历史不足，暂不判断为轮动候选"


def _stability_flags(rows: pd.DataFrame, position: int) -> tuple[dict[str, bool], int]:
    """冷却完成要求三项中至少两项稳定，不能只按经过天数判断。"""
    row = rows.iloc[position]
    recent = rows.iloc[max(0, position - 1):position + 1]
    no_new_low = len(recent) >= 2 and not bool(recent["new_5d_low"].any())

    previous_return = rows.iloc[position - 1]["return_3d"] if position > 0 else float("nan")
    decline_narrowing = bool(
        row["return_3d"] >= -2
        or (pd.notna(previous_return) and row["return_3d"] > previous_return)
    )
    volume_normal = bool(
        pd.notna(row["volume_ratio_20d"]) and row["volume_ratio_20d"] <= 1.10
    )
    flags = {
        "未继续创新低": no_new_low,
        "跌幅收窄": decline_narrowing,
        "成交量恢复正常": volume_normal,
    }
    return flags, sum(flags.values())


def _cooling_priority(
    cooling_days: int,
    minimum_days: int,
    stability: dict[str, bool],
    has_previous_cycle: bool,
    state: str,
) -> tuple[int, bool]:
    """只为真正处于“待轮动”的板块计算优先级；它不是上涨概率。"""
    if not has_previous_cycle:
        return 0, False
    minimum_reached = cooling_days >= minimum_days
    if state != STATE_WAITING:
        return 0, minimum_reached
    time_points = 50 if minimum_reached else 0
    priority = time_points
    priority += 20 if stability["未继续创新低"] else 0
    priority += 15 if stability["跌幅收窄"] else 0
    priority += 15 if stability["成交量恢复正常"] else 0
    return min(100, priority), minimum_reached


def analyse_sector_histories(
    histories: dict[str, tuple[str, pd.DataFrame]],
    benchmark_history: pd.DataFrame,
    current_market_regime: str,
    target_date: pd.Timestamp | None = None,
    initial_states: dict[str, dict] | None = None,
    cycle_context: dict[str, dict] | None = None,
    market_records: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """计算全部板块历史并按时间回放三状态。

    ``histories`` 的结构为 ``板块代码: (板块名称, 原始DataFrame)``。该函数不访问
    网络和数据库，因此可以用临时行情验证状态转换。
    """
    benchmark = _prepare_benchmark(benchmark_history)
    frames = []
    names: dict[str, str] = {}
    for sector_id, (sector_name, raw) in histories.items():
        prepared = _add_metrics(_prepare_sector_history(raw), benchmark)
        prepared["sector_id"] = sector_id
        prepared["sector_name"] = sector_name
        frames.append(prepared)
        names[sector_id] = sector_name
    if not frames:
        return [], []

    combined = pd.concat(frames, ignore_index=True)
    if target_date is not None:
        combined = combined[combined["trade_date"] <= target_date]
    # 横向排名每天重新计算，数值越大表示相对全部行业越强。
    combined["strength_percentile"] = combined.groupby("trade_date")["relative_5d"].rank(
        pct=True, method="average"
    ) * 100
    combined["rotation_heat"] = combined.apply(_heat_score, axis=1)

    # 同日行业收益和沪深300统一交给市场状态函数，回放/增量不再各算一套。
    if market_records is None:
        from rotation.market_regime import build_market_timeline
        changes = {day: group["return_1d"] for day, group in combined.groupby("trade_date")}
        market_records = build_market_timeline(benchmark_history, changes, planned_count=len(histories))
    regime_by_date = {day: item["confirmed_regime"] for day, item in market_records.items()}

    daily_records: list[dict] = []
    all_cycles: list[dict] = []
    latest_date = combined["trade_date"].max()

    for sector_id, sector_rows in combined.groupby("sector_id"):
        rows = sector_rows.sort_values("trade_date").tail(HISTORY_TRADING_DAYS).reset_index(drop=True)
        # MA20和相对收益完整后才开始回放，避免初始化前几日的空指标制造状态。
        rows = rows.dropna(subset=["ma20", "relative_5d", "strength_percentile"]).reset_index(drop=True)
        if rows.empty:
            continue

        # 没有历史轮动证据的板块从“冷却中”开始，不能默认视为待轮动。
        previous = (initial_states or {}).get(sector_id)
        context = (cycle_context or {}).get(sector_id, {})
        process_after: pd.Timestamp | None = None
        if previous:
            previous_date = pd.Timestamp(previous["trade_date"])
            if not bool((rows["trade_date"] > previous_date).any()):
                continue
            process_after = previous_date
            state = previous["confirmed_state"]
            state_start = pd.Timestamp(previous["state_start_date"])
            # 确认次数单独持久化，不能从原始/正式状态反推。
            entry_days = int(previous["entry_days"])
            exit_days = int(previous["exit_days"])
            cooling_days = int(previous["cooling_days"])
            last_end = context.get("last_cycle_end")
            last_cycle_end = pd.Timestamp(last_end) if last_end else None
            active_cycle = context.get("active_cycle")
            current_cycle = dict(active_cycle) if active_cycle else None
        else:
            # 没有历史轮动证据的板块从“冷却中”开始，不能默认视为待轮动。
            state = STATE_COOLING
            state_start = rows.iloc[0]["trade_date"]
            entry_days = 0
            exit_days = 0
            cooling_days = 0
            last_cycle_end = None
            current_cycle = None

        for position, row in rows.iterrows():
            date = row["trade_date"]
            if process_after is not None and date <= process_after:
                continue
            is_latest = date == latest_date
            regime = regime_by_date.get(date, "震荡市")
            minimum_cooling_days = COOLING_MIN_DAYS.get(regime, COOLING_MIN_DAYS["震荡市"])
            entry_candidate = _is_entry_candidate(row, regime)
            raw_state = STATE_ROTATING if entry_candidate else state
            transition_reason = ""

            stability, stable_count = _stability_flags(rows, position)
            price_pattern, price_reasons = _classify_price_pattern(rows, position)
            volume_pattern, volume_reasons = _classify_volume_price(rows, position)
            combination_reason = ""

            if regime == "震荡市":
                # 震荡市不再累加热度或等待连续确认：价格曲线和量价关系
                # 同时命中组合规则后，当天直接切换三状态。
                desired_state, combination_reason = _sideways_combination_state(
                    price_pattern, volume_pattern
                )
                previous_state = state
                raw_state = desired_state

                if previous_state == STATE_ROTATING and current_cycle is not None:
                    current_cycle["duration_days"] += 1
                    current_cycle["max_heat"] = max(
                        current_cycle["max_heat"], int(row["rotation_heat"])
                    )
                    if row["close"] > current_cycle["peak_close"]:
                        current_cycle["peak_close"] = float(row["close"])
                        current_cycle["peak_date"] = date.strftime("%Y-%m-%d")
                    current_cycle["max_return"] = round(
                        (current_cycle["peak_close"] / current_cycle["start_close"] - 1) * 100,
                        4,
                    )

                if previous_state == STATE_ROTATING and desired_state != STATE_ROTATING:
                    if current_cycle is not None:
                        current_cycle.update({
                            "end_date": date.strftime("%Y-%m-%d"),
                            "end_reason": combination_reason,
                            "status": "已结束",
                        })
                        all_cycles.append(current_cycle.copy())
                    last_cycle_end = date
                    current_cycle = None
                    cooling_days = 0
                elif previous_state != STATE_ROTATING and desired_state == STATE_ROTATING:
                    current_cycle = {
                        "cycle_id": f"{sector_id}:{date.strftime('%Y-%m-%d')}",
                        "sector_id": sector_id,
                        "start_date": date.strftime("%Y-%m-%d"),
                        "peak_date": date.strftime("%Y-%m-%d"),
                        "end_date": None,
                        "start_close": float(row["close"]),
                        "peak_close": float(row["close"]),
                        "max_return": 0.0,
                        "max_heat": int(row["rotation_heat"]),
                        "duration_days": 1,
                        "end_reason": None,
                        "status": "进行中",
                    }
                    cooling_days = 0
                elif desired_state != STATE_ROTATING and last_cycle_end is not None:
                    cooling_days += 1

                if desired_state != previous_state:
                    state_start = date
                    transition_reason = combination_reason
                state = desired_state
                entry_days = 0
                exit_days = 0

            elif state == STATE_ROTATING:
                assert current_cycle is not None
                current_cycle["duration_days"] += 1
                current_cycle["max_heat"] = max(current_cycle["max_heat"], int(row["rotation_heat"]))
                if row["close"] > current_cycle["peak_close"]:
                    current_cycle["peak_close"] = float(row["close"])
                    current_cycle["peak_date"] = date.strftime("%Y-%m-%d")
                current_cycle["max_return"] = round(
                    (current_cycle["peak_close"] / current_cycle["start_close"] - 1) * 100, 4
                )

                # 热度明显下降或已不再跑赢大盘，都视为轮动转弱；连续两日后退出。
                exit_candidate = (
                    row["rotation_heat"] < HEAT_EXIT
                    or row.get("relative_5d", 0) <= 0
                )
                exit_days = exit_days + 1 if exit_candidate else 0
                cycle_drawdown = (row["close"] / current_cycle["peak_close"] - 1) * 100
                emergency_exit = cycle_drawdown <= -5 and row.get("relative_3d", 0) < 0
                raw_state = STATE_COOLING if exit_candidate else STATE_ROTATING

                if emergency_exit or exit_days >= STATE_CONFIRM_DAYS:
                    state = STATE_COOLING
                    state_start = date
                    cooling_days = 0
                    transition_reason = (
                        "明显回撤且弱于大盘"
                        if emergency_exit
                        else "轮动热度或相对强度连续两日转弱"
                    )
                    current_cycle.update({
                        "end_date": date.strftime("%Y-%m-%d"),
                        "end_reason": transition_reason,
                        "status": "已结束",
                    })
                    all_cycles.append(current_cycle.copy())
                    last_cycle_end = date
                    current_cycle = None
                    entry_days = 0

            else:
                # 从上一轮结束后持续累计交易日；进入待轮动后仍继续累计，
                # 这样超过有效区间时，待轮动状态才能重新回到冷却中。
                if last_cycle_end is not None:
                    cooling_days += 1
                if entry_candidate:
                    entry_days += 1
                else:
                    entry_days = 0

                strong_entry = entry_candidate and row["rotation_heat"] >= HEAT_STRONG_ENTER
                if strong_entry or entry_days >= STATE_CONFIRM_DAYS:
                    state = STATE_ROTATING
                    state_start = date
                    transition_reason = (
                        "轮动热度达到强信号阈值" if strong_entry
                        else "轮动信号连续两日确认"
                    )
                    current_cycle = {
                        "cycle_id": f"{sector_id}:{date.strftime('%Y-%m-%d')}",
                        "sector_id": sector_id,
                        "start_date": date.strftime("%Y-%m-%d"),
                        "peak_date": date.strftime("%Y-%m-%d"),
                        "end_date": None,
                        "start_close": float(row["close"]),
                        "peak_close": float(row["close"]),
                        "max_return": 0.0,
                        "max_heat": int(row["rotation_heat"]),
                        "duration_days": 1,
                        "end_reason": None,
                        "status": "进行中",
                    }
                    cooling_days = 0
                    entry_days = 0
                    exit_days = 0
                    raw_state = STATE_ROTATING
                elif state == STATE_COOLING:
                    raw_state = STATE_COOLING
                    # 经历过上一轮行情、达到最低冷却天数且已经企稳，才进入待轮动。
                    if (
                        last_cycle_end is not None
                        and cooling_days >= minimum_cooling_days
                        and stable_count >= 2
                    ):
                        state = STATE_WAITING
                        state_start = date
                        transition_reason = "达到最低冷却天数，且至少两项稳定条件成立"
                        raw_state = STATE_WAITING
                elif state == STATE_WAITING:
                    raw_state = STATE_WAITING
                    weakened_again = bool(row["new_5d_low"] and row.get("return_3d", 0) < 0)
                    # 已进入待轮动后，不因冷却时间超过上限退出，只检查原有走弱条件。
                    if stable_count < 2 or weakened_again:
                        state = STATE_COOLING
                        state_start = date
                        transition_reason = "企稳条件不足或价格重新转弱"
                        raw_state = STATE_COOLING

            has_cycle = last_cycle_end is not None
            if regime == "震荡市":
                # 新规则不使用分数或最低冷却天数决定状态；字段只保留数据库兼容性。
                priority = 0
                minimum_cooling_days_reached = cooling_days >= minimum_cooling_days
            else:
                priority, minimum_cooling_days_reached = _cooling_priority(
                    cooling_days,
                    minimum_cooling_days,
                    stability,
                    has_previous_cycle=has_cycle,
                    state=state,
                )
            reasons = [
                f"10日价格形态：{price_pattern}",
                f"量价关系：{volume_pattern}",
            ]
            if regime != "震荡市":
                reasons[:0] = [
                    f"轮动热度 {int(row['rotation_heat'])} 分",
                    f"5日相对沪深300 {row['relative_5d']:+.2f}%",
                    f"行业强度分位 {row['strength_percentile']:.1f}%",
                ]
            reasons.extend(price_reasons)
            reasons.extend(volume_reasons)
            if regime == "震荡市":
                reasons.append(f"组合判断：{combination_reason}")
            if regime != "震荡市" and state in (STATE_COOLING, STATE_WAITING) and has_cycle:
                reasons.append(
                    f"已冷却 {cooling_days} 个交易日，{regime}最低冷却要求 {minimum_cooling_days} 日"
                )
                reasons.extend(name for name, passed in stability.items() if passed)
            if transition_reason:
                reasons.append(f"状态变化：{transition_reason}")

            daily_records.append({
                "trade_date": date.strftime("%Y-%m-%d"),
                "sector_id": sector_id,
                "open": _finite(row["open"]),
                "high": _finite(row["high"]),
                "low": _finite(row["low"]),
                "close": _finite(row["close"]),
                "volume": _finite(row["volume"]),
                "amount": _finite(row["amount"]),
                "return_3d": _finite(row["return_3d"]),
                "return_5d": _finite(row["return_5d"]),
                "return_10d": _finite(row["return_10d"]),
                "return_20d": _finite(row["return_20d"]),
                "relative_3d": _finite(row["relative_3d"]),
                "relative_5d": _finite(row["relative_5d"]),
                "relative_10d": _finite(row["relative_10d"]),
                "ma5": _finite(row["ma5"]),
                "ma10": _finite(row["ma10"]),
                "ma20": _finite(row["ma20"]),
                "volume_ratio_20d": _finite(row["volume_ratio_20d"]),
                "drawdown_20d_high": _finite(row["drawdown_20d_high"]),
                "new_5d_low": int(bool(row["new_5d_low"])),
                "strength_percentile": _finite(row["strength_percentile"]),
                "rotation_heat": int(row["rotation_heat"]),
                "price_pattern": price_pattern,
                "volume_price_pattern": volume_pattern,
                "cooling_priority": priority,
                "raw_state": raw_state,
                "confirmed_state": state,
                "entry_days": entry_days,
                "exit_days": exit_days,
                "state_start_date": state_start.strftime("%Y-%m-%d"),
                "cooling_days": cooling_days,
                # 保留既有字段名以兼容数据库；含义改为是否达到最低冷却天数。
                "in_cooling_window": int(minimum_cooling_days_reached),
                "market_regime": regime,
                "data_quality": "每日记录" if is_latest else "历史回放",
                "reasons_json": reasons,
            })

        if current_cycle is not None:
            all_cycles.append(current_cycle.copy())

    return daily_records, all_cycles


def _finite(value: Any) -> float | None:
    """SQLite不保存NaN和无穷值，缺失指标统一写入NULL。"""
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if number not in (float("inf"), float("-inf")) else None


def _get_ths_catalog_and_cookie() -> tuple[list[dict], str]:
    """一次性读取同花顺行业目录和请求凭证，避免每个工作线程启动 JS 引擎。"""
    import py_mini_racer
    import requests
    from akshare.datasets import get_ths_js
    from bs4 import BeautifulSoup

    js_code = py_mini_racer.MiniRacer()
    js_code.eval(get_ths_js("ths.js").read_text(encoding="utf-8"))
    cookie = str(js_code.call("v"))
    response = requests.get(
        "https://q.10jqka.com.cn/thshy/detail/code/881272/",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": f"v={cookie}",
        },
        timeout=(NETWORK_CONNECT_TIMEOUT, NETWORK_READ_TIMEOUT),
    )
    response.raise_for_status()
    container = BeautifulSoup(response.text, features="lxml").find(
        name="div", attrs={"class": "cate_inner"}
    )
    if container is None:
        raise ValueError("同花顺行业目录页面结构异常")

    masters = []
    seen = set()
    for link in container.find_all("a"):
        href = link.get("href") or ""
        parts = [part for part in href.split("/") if part]
        if len(parts) < 2:
            continue
        code = parts[-1] if parts[-1].isdigit() else parts[-2]
        name = link.get_text(strip=True)
        if not name or not code.isdigit() or code in seen:
            continue
        seen.add(code)
        masters.append({"sector_id": code, "sector_name": name, "source": SOURCE_NAME})
    if not masters:
        raise ValueError("同花顺行业目录没有解析出有效板块")
    return masters, cookie


def _fetch_ths_sector_history(
    item: dict, cookie: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """下载一个行业的日线；所有外部请求都有连接和读取超时。"""
    import requests
    from akshare.utils import demjson

    frames = []
    with requests.Session() as session:
        for year in range(start.year, end.year + 1):
            response = session.get(
                f"https://d.10jqka.com.cn/v4/line/bk_{item['sector_id']}/01/{year}.js",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://q.10jqka.com.cn/",
                    "Cookie": f"v={cookie}",
                },
                timeout=(NETWORK_CONNECT_TIMEOUT, NETWORK_READ_TIMEOUT),
            )
            response.raise_for_status()
            start_at = response.text.find("{")
            if start_at < 0:
                continue
            payload = demjson.decode(response.text[start_at:-1])
            data = payload.get("data") if isinstance(payload, dict) else None
            if not data:
                continue
            frame = pd.Series(data.split(";"), dtype="string").str.split(",", expand=True)
            if frame.shape[1] < 7:
                continue
            frame = frame.iloc[:, :7]
            frame.columns = ["日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"]
            frames.append(frame)

    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    result["日期"] = pd.to_datetime(result["日期"], errors="coerce")
    for column in ("开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["日期", "收盘价"])
    return result[
        (result["日期"] >= pd.Timestamp(start.date()))
        & (result["日期"] <= pd.Timestamp(end.date()))
    ].reset_index(drop=True)


def _fetch_benchmark_history() -> pd.DataFrame:
    """从腾讯结构化接口读取沪深300日线，避免新浪全历史 JS 解码长时间阻塞。"""
    import requests
    from akshare.utils import demjson

    end = datetime.now()
    start = end - timedelta(days=400)
    response = requests.get(
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
        params={
            "_var": "kline_dayqfq",
            "param": f"sh000300,day,{start:%Y-%m-%d},{end:%Y-%m-%d},640,qfq",
        },
        timeout=(NETWORK_CONNECT_TIMEOUT, NETWORK_READ_TIMEOUT),
    )
    response.raise_for_status()
    start_at = response.text.find("={")
    if start_at < 0:
        raise ValueError("腾讯沪深300响应格式异常")
    payload = demjson.decode(response.text[start_at + 1:])
    market_data = payload.get("data", {}).get("sh000300", {})
    values = market_data.get("day") or market_data.get("qfqday") or []
    if not values:
        raise ValueError("腾讯沪深300历史行情为空")
    result = pd.DataFrame(values).iloc[:, :6]
    result.columns = ["date", "open", "close", "high", "low", "amount"]
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ("open", "close", "high", "low", "amount"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["date", "close"]).drop_duplicates("date").reset_index(drop=True)


def _fetch_histories() -> tuple[list[dict], dict[str, tuple[str, pd.DataFrame]], list[str]]:
    """有限并发获取行业历史，并用请求超时和总预算阻止无限等待。"""
    masters, cookie = _get_ths_catalog_and_cookie()
    end = datetime.now()
    start = end - timedelta(days=200)

    def fetch_one(item: dict) -> tuple[str, str, pd.DataFrame]:
        frame = _fetch_ths_sector_history(item, cookie, start, end)
        return item["sector_id"], item["sector_name"], frame

    histories: dict[str, tuple[str, pd.DataFrame]] = {}
    failures: list[str] = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS)
    try:
        futures = {executor.submit(fetch_one, item): item for item in masters}
        completed = 0
        deadline = time.monotonic() + HISTORY_FETCH_BUDGET_SECONDS
        pending = set(futures)
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = concurrent.futures.wait(
                pending,
                timeout=min(5.0, remaining),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                item = futures[future]
                completed += 1
                try:
                    sector_id, name, frame = future.result()
                    # 在获取阶段就校验，避免空表被误计入覆盖率。
                    _prepare_sector_history(frame)
                    histories[sector_id] = (name, frame)
                except Exception as exc:
                    logger.warning("板块 %s 获取失败: %s", item["sector_name"], exc)
                    failures.append(item["sector_name"])
                if completed % 10 == 0 or completed == len(masters):
                    logger.info("板块行情获取进度：%d/%d", completed, len(masters))

        if pending:
            logger.warning(
                "板块行情获取达到 %ds 总时限，取消剩余 %d 个请求",
                HISTORY_FETCH_BUDGET_SECONDS,
                len(pending),
            )
            for future in pending:
                future.cancel()
                failures.append(futures[future]["sector_name"])
    finally:
        # 正在执行的 requests 最多再等待一次读取超时；排队任务会立即取消。
        executor.shutdown(wait=False, cancel_futures=True)
    return masters, histories, failures


def _coverage_target(
    histories: dict[str, tuple[str, pd.DataFrame]], planned_count: int
) -> tuple[pd.Timestamp | None, set[str], float]:
    """选择至少80%板块共同具备的最新交易日，处理数据源晚一天的情况。"""
    dates_by_sector: dict[str, set[pd.Timestamp]] = {}
    counts: dict[pd.Timestamp, int] = {}
    for sector_id, (_, raw) in histories.items():
        prepared = _prepare_sector_history(raw)
        dates = set(prepared["trade_date"])
        dates_by_sector[sector_id] = dates
        for date in dates:
            counts[date] = counts.get(date, 0) + 1
    eligible = [date for date, count in counts.items() if count / planned_count >= MIN_DATA_COVERAGE]
    target = max(eligible) if eligible else (max(counts) if counts else None)
    present = {sector_id for sector_id, dates in dates_by_sector.items() if target in dates}
    coverage = len(present) / planned_count if planned_count else 0.0
    return target, present, coverage


def update_sector_states(
    storage: RotationStorage | None = None,
    histories: dict[str, tuple[str, pd.DataFrame]] | None = None,
    benchmark_history: pd.DataFrame | None = None,
    force_rebuild: bool = False,
) -> dict:
    """增量更新行业状态；force_rebuild 用于规则变化后的完整历史重算。"""
    store = storage or RotationStorage()
    from rotation.calendar import completed_trade_date
    from rotation.market_regime import MARKET_SOURCE

    # 仅处理北京时间已经收盘的日线；无法核实日历时停止更新，保留已有记录。
    cutoff = pd.Timestamp(completed_trade_date())
    cutoff_text = cutoff.strftime("%Y-%m-%d")

    # 自动联网刷新时先检查本地结果。当天数据完整且算法字段齐全，就直接返回，
    # 避免每次 cron 或重复命令都重新下载90个板块后才发现无需更新。
    if histories is None and benchmark_history is None and not force_rebuild:
        cached_run = store.get_latest_update_run()
        cached_market = store.get_market_on_or_before(cutoff_text)
        cached_states = [
            item for item in store.list_sector_states()
            if item["trade_date"] == cutoff_text
        ]
        cache_complete = (
            cached_run is not None
            and cached_run["trade_date"] == cutoff_text
            and cached_run["status"] == "完整"
            and cached_market is not None
            and cached_market["trade_date"] == cutoff_text
            and cached_market["data_source"] == MARKET_SOURCE
            and len(cached_states) >= cached_run["success_count"]
            and all(
                item.get("price_pattern") is not None
                and item.get("volume_price_pattern") is not None
                for item in cached_states
            )
        )
        if cache_complete:
            counts = {
                state: sum(item["confirmed_state"] == state for item in cached_states)
                for state in (STATE_ROTATING, STATE_COOLING, STATE_WAITING)
            }
            return {
                **cached_run,
                "market_regime": cached_market["confirmed_regime"],
                "market_date_note": "市场与板块均使用同日收盘数据",
                "state_counts": counts,
                "message": "目标交易日已经更新，直接使用本地结果",
            }

    if histories is None:
        masters, histories, fetch_failures = _fetch_histories()
    else:
        masters = [
            {"sector_id": code, "sector_name": name, "source": "传入行情"}
            for code, (name, _) in histories.items()
        ]
        fetch_failures = []

    if benchmark_history is None:
        benchmark_history = _fetch_benchmark_history()

    benchmark_history = benchmark_history.copy()
    benchmark_date = "date" if "date" in benchmark_history else "日期"
    benchmark_history = benchmark_history[pd.to_datetime(benchmark_history[benchmark_date]) <= cutoff]
    if benchmark_history.empty:
        raise ValueError("没有已收盘的沪深300行情")
    cutoff = min(cutoff, pd.to_datetime(benchmark_history[benchmark_date]).max())
    histories = {code: (name, frame[pd.to_datetime(frame["日期"]) <= cutoff].copy())
                 for code, (name, frame) in histories.items()}
    target, present, coverage = _coverage_target(histories, len(masters))
    target_text = target.strftime("%Y-%m-%d") if target is not None else datetime.now().strftime("%Y-%m-%d")
    missing_names = [
        item["sector_name"] for item in masters if item["sector_id"] not in present
    ]
    failed = sorted(set(fetch_failures + missing_names))
    run = {
        "trade_date": target_text,
        "planned_count": len(masters),
        "success_count": len(present),
        "failed_count": len(failed),
        "coverage": round(coverage, 6),
        "failed": failed,
        "status": "完整" if coverage >= MIN_DATA_COVERAGE else "数据不足",
    }

    if target is None or coverage < MIN_DATA_COVERAGE:
        store.save_sector_analysis(masters, [], [], run)
        return {**run, "message": "板块数据覆盖率不足，未切换任何板块状态"}

    usable = {code: value for code, value in histories.items() if code in present}
    from rotation.market_regime import build_market_timeline
    old_market = store.get_market_on_or_before(target_text)
    if old_market and old_market["data_source"] != MARKET_SOURCE:
        force_rebuild = True
    # 横向广度只由相同日期的行业收盘涨跌构成，完全移除实时快照输入。
    changes_frames = []
    for code, (_, frame) in usable.items():
        prepared = _prepare_sector_history(frame)
        prepared["change"] = prepared["close"].pct_change(fill_method=None) * 100
        changes_frames.append(prepared[["trade_date", "change"]])
    changes_frame = pd.concat(changes_frames, ignore_index=True)
    changes = {day: group["change"] for day, group in changes_frame.groupby("trade_date")}
    aligned_benchmark = benchmark_history[pd.to_datetime(benchmark_history[benchmark_date]) <= target]
    market_records = build_market_timeline(aligned_benchmark, changes, store, len(masters))
    market = market_records.get(target)
    if not market or market["raw_regime"] == "数据不足":
        raise ValueError("目标日期的指数与行业收盘数据不足，未更新板块状态")
    regime = market["confirmed_regime"]
    market_date_note = "市场与板块均使用同日收盘数据"

    existing_latest = store.list_sector_states()
    # 自动迁移旧算法结果。新增形态字段为空时必须回放，否则同一交易日会继续复用旧状态。
    if any(
        item.get("entry_days") is None
        or item.get("exit_days") is None
        or item.get("price_pattern") is None
        or item.get("volume_price_pattern") is None
        for item in existing_latest
    ):
        force_rebuild = True
    latest_run = store.get_latest_update_run()
    failed_last_time = set(
        latest_run["failed"]
        if latest_run and latest_run["trade_date"] == target_text
        else []
    )
    existing_on_target = {
        item["sector_id"]: item for item in existing_latest
        if item["trade_date"] == target_text and item["sector_name"] not in failed_last_time
    }
    if not force_rebuild and present.issubset(existing_on_target):
        # 同一交易日重复刷新直接复用结果，避免进行中周期的持续天数重复累加。
        latest = [existing_on_target[sector_id] for sector_id in present]
        counts = {state: sum(item["confirmed_state"] == state for item in latest) for state in (
            STATE_ROTATING, STATE_COOLING, STATE_WAITING,
        )}
        return {
            **run,
            "market_regime": regime,
            "market_date_note": market_date_note,
            "state_counts": counts,
            "message": "目标交易日已经更新，无需重复计算",
        }
    if not force_rebuild and existing_on_target:
        # 当天曾经只更新了部分板块，完整回放比混用新旧周期更可靠。
        force_rebuild = True

    initial_states = {} if force_rebuild else store.get_sector_states_before(target_text)
    cycle_context = {} if force_rebuild else store.get_cycle_context()
    daily_records, cycles = analyse_sector_histories(
        usable,
        benchmark_history,
        regime,
        target_date=target,
        initial_states=initial_states,
        cycle_context=cycle_context,
        market_records=market_records,
    )
    store.save_sector_analysis(masters, daily_records, cycles, run)
    # 统计只使用本次目标交易日，获取失败的板块不会以旧状态混入当天结果。
    latest = [
        item for item in store.list_sector_states()
        if item["trade_date"] == target_text and item["sector_id"] in present
    ]
    counts = {state: sum(item["confirmed_state"] == state for item in latest) for state in (
        STATE_ROTATING, STATE_COOLING, STATE_WAITING,
    )}
    return {
        **run,
        "market_regime": regime,
        "market_date_note": market_date_note,
        "state_counts": counts,
        "hottest": [] if regime == "震荡市" else [
            {"sector": item["sector_name"], "heat": item["rotation_heat"]}
            for item in sorted(latest, key=lambda item: item["rotation_heat"], reverse=True)[:5]
        ],
        "cooling_candidates": [
            {
                "sector": item["sector_name"],
                "state": item["confirmed_state"],
                "cooling_days": item["cooling_days"],
                "priority": item["cooling_priority"],
            }
            for item in sorted(latest, key=lambda item: item["cooling_priority"], reverse=True)[:5]
            if item["confirmed_state"] == STATE_WAITING and item["cooling_priority"] > 0
        ],
    }


def get_sector_state(sector_name: str, storage: RotationStorage | None = None) -> dict | None:
    """查看一个行业板块的最新三状态结果。"""
    return (storage or RotationStorage()).get_sector_state(sector_name)


def list_sector_states(state: str | None = None, storage: RotationStorage | None = None) -> list[dict]:
    """列出全部或某一状态的行业板块。"""
    if state and state not in (STATE_ROTATING, STATE_COOLING, STATE_WAITING):
        raise ValueError("state 只能是：轮动中、冷却中、待轮动")
    return (storage or RotationStorage()).list_sector_states(state)


def main() -> None:
    """手动执行：python -m rotation.sector_state。"""
    print(json.dumps(update_sector_states(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
