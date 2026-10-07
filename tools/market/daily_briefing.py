"""读取项目生成的本地每日日报。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from langchain_core.tools import tool


REPORT_DIR = Path(__file__).resolve().parents[2] / "cron" / "reports"


def _parse_target(target_date: str) -> date:
    try:
        return datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else date.today()
    except ValueError as exc:
        raise ValueError("target_date 必须是 YYYY-MM-DD 格式") from exc


@tool
def get_project_daily_report(target_date: str = "") -> dict:
    """读取项目本地生成的每日日报正文。

    用户分析当天可能异动、轮动或值得观察的板块时调用。只读取 cron/reports
    下目标日期或此前3天最近一份成功日报，不联网，也不读取财联社早报。
    日报正文会直接返回，由模型自行总结并结合板块状态判断。
    """
    target = _parse_target(target_date)
    for offset in range(4):
        report_date = target - timedelta(days=offset)
        path = REPORT_DIR / f"{report_date.isoformat()}.md"
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content or "日报生成失败" in content:
            continue
        return {
            "status": "可用",
            "source": "项目每日日报",
            "report_date": report_date.isoformat(),
            "path": str(path),
            "content": content,
        }
    return {
        "status": "不可用",
        "source": "项目每日日报",
        "reason": "目标日期及此前3天没有成功生成的日报",
    }
