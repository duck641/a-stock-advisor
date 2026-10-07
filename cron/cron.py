#!/home/newuser/projects/A_stock/.venv/bin/python3
"""
LangGraph Agent 定时任务调度器

每天早上 8:00 运行 A_stock Agent，分析板块并生成推荐报告。
报告保存到 cron/reports/YYYY-MM-DD.md。

用法:
  # 后台运行
  nohup .venv/bin/python3 cron/cron.py > /dev/null 2>&1 &

  # 立即执行一次
  .venv/bin/python3 cron/cron.py --now

  # 停止
  pkill -f "cron/cron.py"
"""

import sys, os, time, logging
from datetime import datetime
import schedule

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
os.makedirs(REPORT_DIR, exist_ok=True)


def refresh_rotation_data():
    """先更新大盘和板块状态，保证日报使用最近一个交易日的数据。"""
    from rotation.storage import RotationStorage
    from rotation.sector_state import update_sector_states

    sectors = update_sector_states()
    market = RotationStorage().get_market_on_or_before(sectors["trade_date"])
    if not market:
        raise ValueError("本次刷新没有可用的市场状态")
    logger.info(
        "轮动数据已更新：市场日期=%s，板块日期=%s，覆盖率=%.1f%%",
        market["trade_date"], sectors["trade_date"], sectors["coverage"] * 100,
    )
    return {"market": market, "sectors": sectors}


def generate_report():
    """更新轮动数据后生成结构紧凑的每日日报。"""
    from agent import AGENT_RUN_CONFIG, build_agent
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from prompts import build_react_system_prompt

    today = datetime.now().strftime("%Y-%m-%d")
    report_file = os.path.join(REPORT_DIR, f"{today}.md")

    refresh_note = ""
    try:
        refreshed = refresh_rotation_data()
        refresh_note = (
            f"市场数据日期：{refreshed['market']['trade_date']}；"
            f"板块数据日期：{refreshed['sectors']['trade_date']}；"
            f"覆盖率：{refreshed['sectors']['coverage']:.1%}。"
        )
    except Exception as exc:
        # 行情更新失败时仍尝试生成日报，但在日志中明确记录，不能伪装成最新状态。
        logger.exception("[%s] 轮动数据更新失败: %s", today, exc)
        refresh_note = "本次行情刷新失败，使用任何历史数据都必须注明日期，不能声称已更新。"

    prompt = (
        f"今天是 {today}。{refresh_note}\n\n"
        "请帮我完成以下分析。本次任务不要调用 get_project_daily_report，以免读取正在生成的本地日报；可以调用 get_cls_morning_report 获取财联社早报：\n"
        "1. 查询未来5天的重要事件与必要的大盘信息。\n"
        "2. 提取可能影响行业的正面、负面和不确定线索，查询相关行业轮动状态。\n"
        "3. 最多列出3个有依据的观察方向，依据不足可以少列或不列，不强制挑选个股。\n"
        "4. 数据查询失败或不足时说明缺口，不反复尝试相同查询，及时完成报告。\n\n"
        "请以「📊 A股每日早报」为标题，按下面固定结构输出：\n"
        "## 大盘环境（不超过3条）\n"
        "## 可能影响板块的线索（写明事件、板块、理由和日期）\n"
        "## 当前主要风险（不超过3条）\n"
        "## 今日需要验证的数据（不超过5条）\n"
        "全文控制在1500至2500个中文字，删除重复行情描述和没有形成结论的工具原始输出。"
    )

    messages = [
        SystemMessage(content=build_react_system_prompt(
            role="analyst",
            include_rules=["base", "risk", "stock_resolve"],
            include_skills=["trend", "indicator", "volume"],
        )),
        HumanMessage(content=prompt),
    ]

    try:
        agent = build_agent()
        final_content = ""
        for chunk in agent.stream(
            {"messages": messages}, config=AGENT_RUN_CONFIG, stream_mode="updates"
        ):
            for node_name, value in chunk.items():
                if not isinstance(value, dict) or "messages" not in value:
                    continue
                for msg in value["messages"]:
                    if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                        final_content = msg.content

        if final_content:
            # 先写临时文件再替换，生成失败不会覆盖同一天已有的有效日报。
            from pathlib import Path
            from tempfile import NamedTemporaryFile
            if not isinstance(final_content, str):
                final_content = "\n".join(
                    part if isinstance(part, str) else part.get("text", "")
                    for part in final_content if isinstance(part, (str, dict))
                )
            if not final_content.strip():
                raise ValueError("模型没有返回可保存的报告正文")
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=REPORT_DIR, suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                f.write(final_content + "\n")
            try:
                temporary.replace(report_file)
            finally:
                temporary.unlink(missing_ok=True)
            logger.info("日报已生成 → %s", report_file)
            print(final_content)
            return True
        else:
            raise ValueError("Agent 未生成有效内容")

    except Exception as e:
        logger.error("[%s] 日报生成失败: %s", today, e)
        with open(report_file + ".error.log", "w", encoding="utf-8") as f:
            f.write(f"[{today}] 日报生成失败: {e}\n")
        return False


# ════════════════════════════════════════════════
# 定时任务注册
# ════════════════════════════════════════════════

if __name__ == "__main__":
    schedule.every().day.at("09:00").do(generate_report)

    if "--now" in sys.argv:
        generate_report()
    else:
        while True:
            schedule.run_pending()
            time.sleep(30)
