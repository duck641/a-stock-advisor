"""A股市场状态识别。

判断过程全部由可检查的行情指标完成，LLM 不参与牛熊判断：
  1. 沪深300与 MA20、MA60 的位置决定中期趋势；
  2. MA20 最近三个交易日的变化决定均线方向；
  3. 20日收益确认趋势已经形成足够幅度；
  4. 行业板块上涨占比验证行情是否具有市场广度；
  5. 牛熊信号连续三个交易日才成立；严格牛熊条件失效时立即回到震荡市。
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from rotation.storage import MarketRegimeStorage


BENCHMARK_CODE = "sh000300"
BENCHMARK_NAME = "沪深300"
CONFIRM_DAYS = 3

# 牛熊市只描述方向明确、持续性较强的市场环境。均线仅仅交叉不够，指数位置、
# 均线间距、20日累计涨跌和行业广度必须同时满足；其余情况统一归入震荡市。
PRICE_MA20_BUFFER = 0.01
MA20_MA60_GAP = 0.005
MA20_SLOPE_3D_PERCENT = 0.20
BULL_RETURN_20D = 5.0
BEAR_RETURN_20D = -5.0
BULL_SECTOR_BREADTH = 0.60
BEAR_SECTOR_BREADTH = 0.25

REGIME_BULL = "牛市"
REGIME_SIDEWAYS = "震荡市"
REGIME_BEAR = "熊市"
REGIME_INSUFFICIENT = "数据不足"
MARKET_SOURCE = "腾讯沪深300日线 + 同花顺行业收盘广度 v4"


def _number_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise ValueError(f"行情数据缺少 {column} 列")
    return pd.to_numeric(frame[column], errors="coerce")


def _prepare_index_history(frame: pd.DataFrame) -> pd.DataFrame:
    """兼容行情接口的中英文列名，并保证指标按日期顺序计算。"""
    if frame is None or frame.empty:
        raise ValueError("沪深300历史行情为空")

    result = frame.copy()
    date_column = "date" if "date" in result.columns else "日期"
    close_column = "close" if "close" in result.columns else "收盘"
    if date_column not in result.columns:
        raise ValueError("沪深300行情缺少日期列")

    result["trade_date"] = pd.to_datetime(result[date_column], errors="coerce")
    result["close_value"] = _number_series(result, close_column)
    result = (
        result.dropna(subset=["trade_date", "close_value"])
        .sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
    )
    if len(result) < 63:
        raise ValueError(f"沪深300有效历史数据不足63个交易日，当前只有 {len(result)} 条")
    return result


def _sector_breadth(frame: pd.DataFrame | None) -> tuple[float | None, int]:
    """计算行业板块上涨比例；缺失时返回 None，不伪造市场广度。"""
    if frame is None or frame.empty or "涨跌幅" not in frame.columns:
        return None, 0
    changes = pd.to_numeric(frame["涨跌幅"], errors="coerce").dropna()
    if changes.empty:
        return None, 0
    return float((changes > 0).sum() / len(changes)), int(len(changes))


def classify_market_regime(
    index_history: pd.DataFrame,
    sector_snapshot: pd.DataFrame | None,
) -> dict[str, Any]:
    """根据指数趋势和板块广度生成当日原始市场状态。

    这是纯计算函数，方便用历史或离线数据验证。三日确认在保存前单独完成，
    因此这里不会读取数据库。
    """
    history = _prepare_index_history(index_history)
    close = history["close_value"]
    ma20_series = close.rolling(20).mean()
    ma60_series = close.rolling(60).mean()

    latest_close = float(close.iloc[-1])
    ma20 = float(ma20_series.iloc[-1])
    ma60 = float(ma60_series.iloc[-1])
    ma20_change_3d = float(ma20_series.iloc[-1] - ma20_series.iloc[-4])
    ma20_change_3d_percent = ma20_change_3d / ma20 * 100 if ma20 else 0.0
    previous_20 = float(close.iloc[-21])
    return_20d = ((latest_close / previous_20) - 1) * 100 if previous_20 else None
    up_ratio, sector_count = _sector_breadth(sector_snapshot)

    reasons = [
        f"沪深300收盘 {latest_close:.2f}，MA20 {ma20:.2f}，MA60 {ma60:.2f}",
        f"MA20最近3个交易日变化 {ma20_change_3d:+.2f}",
        f"沪深300近20日收益 {return_20d:+.2f}%",
    ]

    # 缺少板块广度时不强行判断牛熊，避免单一指数掩盖多数板块的真实状态。
    if up_ratio is None:
        raw_regime = REGIME_INSUFFICIENT
        data_status = "板块广度缺失"
        reasons.append("未取得有效行业涨跌数据，本日信号不参与状态切换")
    else:
        reasons.append(f"上涨行业占比 {up_ratio:.1%}（有效行业 {sector_count} 个）")
        bull = (
            latest_close >= ma20 * (1 + PRICE_MA20_BUFFER)
            and ma20 >= ma60 * (1 + MA20_MA60_GAP)
            and ma20_change_3d_percent >= MA20_SLOPE_3D_PERCENT
            and return_20d >= BULL_RETURN_20D
            and up_ratio >= BULL_SECTOR_BREADTH
        )
        bear = (
            latest_close <= ma20 * (1 - PRICE_MA20_BUFFER)
            and ma20 <= ma60 * (1 - MA20_MA60_GAP)
            and ma20_change_3d_percent <= -MA20_SLOPE_3D_PERCENT
            and return_20d <= BEAR_RETURN_20D
            and up_ratio <= BEAR_SECTOR_BREADTH
        )
        if bull:
            raw_regime = REGIME_BULL
        elif bear:
            raw_regime = REGIME_BEAR
        else:
            raw_regime = REGIME_SIDEWAYS
        data_status = "完整"

    return {
        "trade_date": history["trade_date"].iloc[-1].strftime("%Y-%m-%d"),
        "benchmark_code": BENCHMARK_CODE,
        "benchmark_name": BENCHMARK_NAME,
        "close": round(latest_close, 4),
        "ma20": round(ma20, 4),
        "ma60": round(ma60, 4),
        "ma20_change_3d": round(ma20_change_3d, 4),
        "return_20d": round(return_20d, 4) if return_20d is not None else None,
        "sector_up_ratio": round(up_ratio, 6) if up_ratio is not None else None,
        "sector_count": sector_count,
        "raw_regime": raw_regime,
        "data_status": data_status,
        "reasons": reasons,
        "data_source": MARKET_SOURCE,
    }


def _confirm_regime(raw_regime: str, recent: list[dict]) -> tuple[str, int]:
    """牛熊连续三天才成立；牛熊条件失效时立即回到震荡市。"""
    previous_confirmed = recent[0]["confirmed_regime"] if recent else REGIME_SIDEWAYS
    if raw_regime == REGIME_INSUFFICIENT:
        return previous_confirmed, 0
    if raw_regime == REGIME_SIDEWAYS:
        return REGIME_SIDEWAYS, 1

    confirmation_days = 1
    for item in recent:
        if item["raw_regime"] != raw_regime:
            break
        confirmation_days += 1

    if confirmation_days >= CONFIRM_DAYS:
        return raw_regime, confirmation_days
    # 新牛熊信号确认期间属于过渡阶段，不能继续沿用已经失效的旧牛熊状态。
    return raw_regime if previous_confirmed == raw_regime else REGIME_SIDEWAYS, confirmation_days


def update_market_regime(
    storage: MarketRegimeStorage | None = None,
    index_history: pd.DataFrame | None = None,
    sector_snapshot: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """通过统一行业收盘更新入口保存市场状态；离线计算使用 build_market_timeline。"""
    if sector_snapshot is not None:
        raise ValueError("正式市场状态不再接受未对齐日期的实时快照，请使用行业历史收盘数据更新")
    # 统一刷新入口只获取一次行业数据，同时更新市场与板块。
    from rotation.sector_state import update_sector_states
    store = storage or MarketRegimeStorage()
    result = update_sector_states(storage=store, benchmark_history=index_history)
    record = store.get_market_on_or_before(result["trade_date"])
    if not record:
        raise ValueError("没有足够的同日收盘数据生成市场状态")
    return record


def build_market_timeline(index_history, sector_changes, storage=None, planned_count=None):
    """历史回放和实时收盘更新共用；已有同口径正式记录优先，禁止未来数据回填过去。"""
    history = _prepare_index_history(index_history)
    saved = {}
    if storage and not history.empty:
        end = (history["trade_date"].max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        saved = {r["trade_date"]: r for r in storage.get_recent_before(end, limit=10000)
                 if r["data_source"] == MARKET_SOURCE}
    start_date = min(sector_changes) if sector_changes else history["trade_date"].max()
    recent = sorted((r for key, r in saved.items() if key < start_date.strftime("%Y-%m-%d")),
                    key=lambda r: r["trade_date"], reverse=True)[:2]
    records = {}
    start_position = max(62, int(history["trade_date"].searchsorted(start_date)))
    for position in range(start_position, len(history)):
        day = history.iloc[position]["trade_date"]
        key = day.strftime("%Y-%m-%d")
        if key in saved:
            record = saved[key]
        else:
            values = sector_changes.get(day, pd.Series(dtype=float)).dropna()
            if planned_count and len(values) / planned_count < 0.80:
                values = pd.Series(dtype=float)
            snapshot = pd.DataFrame({"涨跌幅": values.to_numpy()})
            record = classify_market_regime(history.iloc[position - 62:position + 1], snapshot)
            confirmed, count = _confirm_regime(record["raw_regime"], recent[:2])
            record.update(confirmed_regime=confirmed, confirmation_days=count)
            # 只保存具有行业历史覆盖的日期；缺失日仍会打断三日连续确认。
            if storage and len(values):
                storage.upsert_market_day(record)
        records[day] = record
        recent.insert(0, record)
        recent = recent[:2]
    return records


def main() -> None:
    """允许使用 python -m rotation.market_regime 手动更新并查看结果。"""
    result = update_market_regime()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
