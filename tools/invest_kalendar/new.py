"""
财联社投资日历工具 - 获取未来重要财经事件、经济数据、会议日程

数据源：财联社（cls.cn）投资日历 API

依赖声明：
  - requests（直连财联社 API）
"""

from langchain_core.tools import tool
from datetime import datetime


@tool
def get_investment_calendar(days: int = 7) -> str:
    """
    获取财联社投资日历，查看未来一段时间的重要财经事件。

    包含：经济数据公布、公司财报、行业会议、政策实施、新股/债发行等。
    重要程度用星级（1-5星）标识，5星为最重要。

    参数:
        days: 查看未来多少天的日历，默认 7 天

    返回:
        投资日历信息（日期 + 星期 + 事件列表 + 重要性星级）
    """
    import requests

    url = "https://www.cls.cn/api/calendar/web/list"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.cls.cn/investkalendar",
    }

    resp = requests.get(url, params={"type": "0"}, headers=headers, timeout=15)
    if resp.status_code != 200:
        return f"获取投资日历失败: HTTP {resp.status_code}"

    data = resp.json()
    if data.get("code") != 200 or not data.get("data"):
        return "投资日历数据为空"

    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    lines = ["📅 财联社投资日历", ""]

    for day_data in data["data"]:
        day = day_data.get("calendar_day", "")
        week = day_data.get("week", "")
        items = day_data.get("items", [])

        if not items:
            continue

        # 按天数过滤（用日期比较，避免 timedelta.days 负值问题）
        try:
            day_date = datetime.strptime(day, "%Y-%m-%d")
            delta = (day_date - now).days
            if delta < 0 or delta > days:
                continue
        except (ValueError, TypeError):
            pass

        lines.append(f"📍 {day} {week}（共 {len(items)} 条）")

        for item in items:
            title = item.get("title", "") or item.get("event", {}).get("title", "")
            star = item.get("event", {}).get("star", 0) if item.get("event") else 0
            country = item.get("event", {}).get("country", "") if item.get("event") else ""

            star_str = "⭐" * star if star else ""
            country_str = f"[{country}] " if country else ""

            if title:
                lines.append(f"  {star_str} {country_str}{title}")

        lines.append("")

    return "\n".join(lines).strip() or f"暂无未来 {days} 天的投资日历数据"
