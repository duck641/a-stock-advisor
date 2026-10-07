"""收盘日口径：以沪市交易日历和北京时间15:00为准。"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHINA_TZ = timezone(timedelta(hours=8))
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_calendar.json"


def completed_trade_date(now=None, trading_dates=None) -> str:
    """返回最近已收盘交易日；日历无法覆盖今天时明确失败，不按周一至周五猜测。"""
    now = now or datetime.now(CHINA_TZ)
    now = now.replace(tzinfo=CHINA_TZ) if now.tzinfo is None else now.astimezone(CHINA_TZ)
    today = now.date().isoformat()
    if trading_dates is None:
        dates = []
        if CACHE_PATH.exists():
            try:
                dates = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        if not dates or max(dates) < today:
            # 用有超时的原始请求读取 AkShare 所用日历，避免网络故障无限等待。
            import requests
            import py_mini_racer
            from akshare.stock.cons import hk_js_decode

            response = requests.get(
                "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt", timeout=15
            )
            response.raise_for_status()
            decoder = py_mini_racer.MiniRacer()
            decoder.eval(hk_js_decode)
            values = decoder.call("d", response.text.split("=")[1].split(";")[0].replace('"', ""))
            dates = sorted({str(value)[:10] for value in values})
            if not dates or max(dates) < today:
                raise ValueError("交易日历未覆盖当前日期，无法确认最近收盘日")
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(dates), encoding="utf-8")
    else:
        dates = sorted({str(value)[:10] for value in trading_dates})
    if not dates or max(dates) < today:
        raise ValueError("交易日历未覆盖当前日期")
    eligible = [day for day in dates if day < today or (day == today and now.hour >= 15)]
    if not eligible:
        raise ValueError("交易日历没有可用的已收盘交易日")
    return max(eligible)


def freshness(data_date, expected_date, failed=False):
    """所有层级共用同一口径；None 表示无法核实，不能声称数据最新。"""
    if failed or not data_date:
        return True
    if expected_date is None:
        return None
    return data_date != expected_date
