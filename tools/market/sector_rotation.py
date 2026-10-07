"""查询与事件线索相关的行业板块轮动状态。"""

from __future__ import annotations

from datetime import date, datetime
from difflib import get_close_matches

from langchain_core.tools import tool

from rotation.calendar import completed_trade_date, freshness
from rotation.config import STATE_COOLING, STATE_ROTATING, STATE_WAITING
from rotation.storage import RotationStorage


@tool
def get_sector_rotation_context(
    sector_names: list[str] | None = None,
    state: str | None = None,
) -> dict:
    """按板块名称查询状态，或按状态列出全部板块。

    两种查询方式必须二选一：
    - 查询指定板块：提供 sector_names，例如 ["半导体", "煤炭"]；名称数量不限。
    - 列出某种状态的全部板块：提供 state，取值为“轮动中”“冷却中”或“待轮动”。

    用户询问“有哪些待轮动/轮动中/冷却中的板块”时使用 state 模式；阅读日报、
    早报或事件并识别出相关行业后使用 sector_names 模式。返回数据日期、新鲜度、
    价格形态、量价关系、状态依据，以及非震荡市仍使用的热度和冷却信息。
    """
    if (sector_names is None) == (state is None):
        raise ValueError("sector_names 和 state 必须且只能提供一个")
    if state is not None and state not in (STATE_ROTATING, STATE_COOLING, STATE_WAITING):
        raise ValueError("state 只能是：轮动中、冷却中、待轮动")
    if sector_names is not None and not sector_names:
        raise ValueError("sector_names 不能为空列表")

    store = RotationStorage()
    all_states = store.list_sector_states()
    # 状态模式由数据库直接筛选；按名称查询则建立名称索引供模糊匹配。
    selected_states = store.list_sector_states(state) if state is not None else all_states
    latest_run = store.get_latest_update_run()
    failed_in_latest_run = set(latest_run["failed"]) if latest_run else set()
    by_name = {item["sector_name"]: item for item in all_states}
    latest_date = max((item["trade_date"] for item in all_states), default=None)
    latest_market_regime = next(
        (item["market_regime"] for item in all_states if item["trade_date"] == latest_date),
        None,
    )
    calendar_error = None
    try:
        expected_date = completed_trade_date()
    except Exception as exc:
        expected_date = None
        calendar_error = f"无法核实交易日: {exc}"
    results = []
    missing = []
    if state is not None:
        for item in selected_states:
            results.append(_format_sector(item, expected_date, failed_in_latest_run))
        if latest_market_regime == "震荡市":
            # 震荡市状态由形态组合直接产生，不再借用旧分数给名单排序。
            results.sort(key=lambda item: item["sector"])
        else:
            sort_key = "waiting_priority" if state == STATE_WAITING else "heat"
            results.sort(key=lambda item: (item[sort_key] or 0, item["heat"] or 0), reverse=True)
    else:
        for requested in dict.fromkeys(name.strip() for name in sector_names if name.strip()):
            matched = requested if requested in by_name else None
            if matched is None:
                contains = [name for name in by_name if requested in name or name in requested]
                matched = contains[0] if len(contains) == 1 else None
            if matched is None:
                suggestions = get_close_matches(requested, by_name.keys(), n=3, cutoff=0.35)
                missing.append({"input": requested, "suggestions": suggestions})
                continue
            item = by_name[matched]
            results.append(_format_sector(item, expected_date, failed_in_latest_run, requested))

    age_days = (
        (date.today() - datetime.strptime(latest_date, "%Y-%m-%d").date()).days
        if latest_date else None
    )
    return {
        "query_mode": "state" if state is not None else "names",
        "requested_state": state,
        "count": len(results),
        "state_date": latest_date,
        "market_regime": latest_market_regime,
        "expected_trade_date": expected_date,
        "calendar_error": calendar_error,
        "is_stale": (
            True if any(item["is_stale"] is True for item in results)
            else freshness(latest_date, expected_date)
        ),
        "age_days": age_days,
        "sectors": results,
        "unmatched": missing,
    }


def _format_sector(
    item: dict,
    expected_date: str | None,
    failed_in_latest_run: set[str],
    requested_name: str | None = None,
) -> dict:
    """统一格式化名称查询和状态列表的板块信息。"""
    result = {
        "sector": item["sector_name"],
        "state": item["confirmed_state"],
        "heat": None if item["market_regime"] == "震荡市" else item["rotation_heat"],
        "price_pattern": item.get("price_pattern"),
        "volume_price_pattern": item.get("volume_price_pattern"),
        "cooling_days": item["cooling_days"],
        "waiting_priority": (
            None if item["market_regime"] == "震荡市" else item["cooling_priority"]
        ),
        "in_cooling_window": bool(item["in_cooling_window"]),
        "state_date": item["trade_date"],
        "is_stale": freshness(
            item["trade_date"], expected_date, item["sector_name"] in failed_in_latest_run
        ),
        "market_regime": item["market_regime"],
        "reasons": item["reasons"],
    }
    if requested_name is not None:
        result["requested_name"] = requested_name
    return result
