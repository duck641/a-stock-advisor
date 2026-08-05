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


def generate_report():
    """执行一次完整的日报分析，输出到日期文件"""
    from agent import agent
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from prompts import build_react_system_prompt

    today = datetime.now().strftime("%Y-%m-%d")
    report_file = os.path.join(REPORT_DIR, f"{today}.md")

    prompt = (
        f"今天是 {today}，现在是早上开盘时间。\n\n"
        "请帮我完成以下分析：\n"
        "1. 调用 get_investment_calendar查看从今天开始未来5天有什么影响股价的重要事件，并把全部事件写道日志里面\n"
        "2. 你需要对以上事件进行分析，将事件分为三类：明显利好股市的事件，中性事件不确定结果，明显利空事件（以行业板块为主，概念板块为辅）\n"
        "3. 事件的发生通常有利有弊，你要综合判断一下这个事件到底是有利于板块还是不利于板块\n"
        "2. 基于以上事件及其利弊分析，综合分析板块历史给出你推荐的3个板块\n"
        "3. 基于你挑选的板块，首先考虑从龙虎榜上综合挑选该板块股票，其次考虑广泛搜索该板块股票找出最近超跌反弹的股票（每个板块至少挑选5支股票并严格对比）\n"
        "4. 给出你今日的完整投资计划\n\n"
        "请以「📊 A股每日早报」为标题，输出完整的分析报告。"
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
        final_content = ""
        for chunk in agent.stream({"messages": messages}, stream_mode="updates"):
            for node_name, value in chunk.items():
                if not isinstance(value, dict) or "messages" not in value:
                    continue
                for msg in value["messages"]:
                    if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                        final_content = msg.content

        if final_content:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(final_content + "\n")
            logger.info("日报已生成 → %s", report_file)
            print(final_content)
        else:
            logger.warning("[%s] Agent 未生成有效内容", today)

    except Exception as e:
        logger.error("[%s] 日报生成失败: %s", today, e)
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(f"[{today}] 日报生成失败: {e}\n")


# ════════════════════════════════════════════════
# 定时任务注册
# ════════════════════════════════════════════════

schedule.every().day.at("09:00").do(generate_report)

if "--now" in sys.argv:
    generate_report()
else:
    while True:
        schedule.run_pending()
        time.sleep(30)
