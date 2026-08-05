"""
A_stock CLI — 统一命令行入口

用法:
  a-stock chat         终端对话
  a-stock web          Web UI（http://localhost:8000）
  a-stock cron         定时日报（后台常驻）
  a-stock cron --now   立即生成一次日报
"""

import argparse
import sys


def cmd_chat():
    """终端连续对话"""
    import logging
    # 控制台只显示 WARNING 及以上，避免操作日志干扰对话交互
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(logging.WARNING)
    from chat import run_chat
    run_chat()


def cmd_web():
    """FastAPI + WebSocket Web UI"""
    import uvicorn
    from wechat.server import app
    uvicorn.run(app, host="0.0.0.0", port=8000)


def cmd_cron(now: bool = False):
    """定时日报"""
    if now:
        # 立即执行一次后退出
        from cron.cron import generate_report
        generate_report()
    else:
        # 后台常驻（每天 09:00 执行）
        import time
        import schedule
        from cron.cron import generate_report
        schedule.every().day.at("09:00").do(generate_report)
        print("⏰ 定时日报已启动，每天 09:00 执行。按 Ctrl+C 停止。")
        try:
            while True:
                schedule.run_pending()
                time.sleep(30)
        except KeyboardInterrupt:
            print("\n👋 已停止")


def cmd_setup():
    """交互式配置向导"""
    from setup_wizard import run_setup
    run_setup()


def main():
    parser = argparse.ArgumentParser(
        prog="a-stock",
        description="A股分析助手 CLI",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")

    # chat
    sub.add_parser("chat", help="终端连续对话")

    # web
    sub.add_parser("web", help="启动 Web UI（http://localhost:8000）")

    # cron
    cron_parser = sub.add_parser("cron", help="定时日报")
    cron_parser.add_argument(
        "--now", action="store_true",
        help="立即生成一次日报（不进入定时循环）",
    )

    # setup
    sub.add_parser("setup", help="交互式配置向导（切换模型/设置 API Key）")

    args = parser.parse_args()

    if args.command is None:
        cmd_chat()
        return

    if args.command == "chat":
        cmd_chat()
    elif args.command == "web":
        cmd_web()
    elif args.command == "cron":
        cmd_cron(now=args.now)
    elif args.command == "setup":
        cmd_setup()


if __name__ == "__main__":
    main()
