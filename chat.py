"""
持久化对话循环 — 聊天界面

从 agent 导入已构建好的 agent，运行终端连续对话。
对话过长时自动压缩早期历史为摘要，避免超长 context。

压缩触发条件：历史消息 token 数 > context_window × compression_ratio
可在 .env 中调整：
  LLM_CONTEXT_WINDOW=65536     # 模型上下文窗口
  LLM_COMPRESSION_RATIO=0.6   # 触发比例
"""
import logging
import os
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from agent import agent, summary_llm, tracker
from config import config
from memory import ChatStorage, estimate_tokens, compress_history
from prompts import build_react_system_prompt

logger = logging.getLogger(__name__)

# 模型上下文限制
_CONTEXT_WINDOW = config.context_window
_COMPRESSION_AT = _CONTEXT_WINDOW * config.compression_ratio


def _init_system_prompt(storage: ChatStorage, conv_id: str):
    """新对话初始化：将 system prompt 写入 messages 表"""
    sp = build_react_system_prompt(
        role="analyst",
        include_rules=["base", "risk", "stock_resolve"],
        include_skills=["trend", "indicator", "volume"],
    )
    storage.save_message(conv_id, SystemMessage(content=sp))


# ════════════════════════════════════════════════
# 对话管理
# ════════════════════════════════════════════════

def _pick_or_create_conversation(storage: ChatStorage) -> str:
    """列出已有对话让用户选择，或创建新对话。"""
    convs = storage.list_conversations()

    if convs:
        print("\n📋 已有对话:")
        for i, c in enumerate(convs, 1):
            summary_hint = " 📝" if c.get("summary", "") else ""
            print(f"  [{i}] {c['title']} ({c['msg_count']} 条消息){summary_hint}")
        print("  [n] 新建对话")
        print()

        choice = input("选择对话编号 (回车=新建): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(convs):
                conv_id = convs[idx]["id"]
                title = convs[idx]["title"]
                hint = "（含摘要）" if convs[idx].get("summary", "") else ""
                print(f"\n--- 恢复对话: {title} {hint}---\n")
                return conv_id

    conv_id = storage.create_conversation()
    _init_system_prompt(storage, conv_id)
    print(f"\n--- 新对话已创建 ---\n")
    return conv_id


def run_chat():
    """终端连续对话 + SQLite 持久化 + 自动历史压缩。"""
    storage = ChatStorage()

    # prompt_toolkit 会话：支持鼠标光标定位、方向键、历史记录
    hist_file = os.path.join(os.path.dirname(__file__), "data", ".chat_history")
    session = PromptSession(history=FileHistory(hist_file))

    print("📊 A 股分析助手")
    print("  命令: /compress 压缩 /title <名称> 重命名  /new 新建  /list 切换  /delete 删除  exit/quit 退出")
    # print("  所有消息自动保存至: data/chat_history.db\n")

    # 选择对话
    conv_id = _pick_or_create_conversation(storage)

    # 加载历史（有摘要时只取最近 KEEP_RECENT 条）
    history = storage.get_messages(conv_id)
    msg_count = len(history)
    needs_title = msg_count == 0 or (
        len(history) == 1 and isinstance(history[0], SystemMessage)
    )
    if msg_count > 0:
        has_summary = any(
            isinstance(m, SystemMessage) and "前情摘要" in (m.content or "")
            for m in history
        )
        print(f"  已加载 {msg_count} 条"
              f"{'（含摘要）' if has_summary else ''}\n")

    while True:
        try:
            user_input = session.prompt(">> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        # ── 命令处理 ──────────────────────────
        if user_input.lower() in ("exit", "quit", "q"):
            break

        if user_input.lower() == "/new":
            conv_id = storage.create_conversation()
            _init_system_prompt(storage, conv_id)
            history = []
            needs_title = True
            print("\n📌 已切换到新对话\n")
            continue

        if user_input.lower() == "/list":
            convs = storage.list_conversations()
            if not convs:
                print("  (暂无保存的对话)\n")
                continue
            print("\n📋 已保存的对话:")
            for i, c in enumerate(convs, 1):
                mark = " ← 当前" if c["id"] == conv_id else ""
                sum_hint = " 📝" if c.get("summary", "") else ""
                print(f"  [{i}] {c['title']} ({c['msg_count']} 条消息){sum_hint}{mark}")
            print()
            choice = input("输入编号切换 (回车=取消): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(convs):
                    conv_id = convs[idx]["id"]
                    history = storage.get_messages(conv_id)
                    has_sum = any("前情摘要" in (m.content or "")
                                  for m in history if isinstance(m, SystemMessage))
                    print(f"  已切换到: {convs[idx]['title']}"
                          f" ({len(history)} 条{'含摘要' if has_sum else ''})\n")
            continue

        if user_input.lower().startswith("/delete"):
            convs = storage.list_conversations()
            if not convs:
                print("  (暂无保存的对话)\n")
                continue
            print("\n📋 选择要删除的对话:")
            for i, c in enumerate(convs, 1):
                print(f"  [{i}] {c['title']} ({c['msg_count']} 条消息)")
            print()
            choice = input("输入编号删除 (回车=取消): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(convs):
                    target_id = convs[idx]["id"]
                    storage.delete_conversation(target_id)
                    print(f"  已删除: {convs[idx]['title']}\n")
                    if target_id == conv_id:
                        conv_id = storage.create_conversation()
                        _init_system_prompt(storage, conv_id)
                        history = []
                        needs_title = True
                        print("  已自动创建新对话\n")
            continue

        if user_input.lower() == "/compress":
            history = compress_history(history, summary_llm)
            storage.replace_all_messages(conv_id, history)
            print(f"  📝 已压缩为 {len(history)} 条消息（含摘要）\n")
            continue

        if user_input.lower().startswith("/title"):
            title = user_input[6:].strip()
            if title:
                storage.update_title(conv_id, title)
                needs_title = False
                print(f"  对话已重命名为: {title}\n")
            else:
                print("  用法: /title <对话名称>\n")
            continue

        if not user_input:
            continue

        # ── 正常对话流程 ──────────────────────────
        if needs_title:
            title = user_input[:30] + ("..." if len(user_input) > 30 else "")
            storage.update_title(conv_id, title)
            needs_title = False

        # 写入用户消息到 DB + history
        user_msg = HumanMessage(content=user_input)
        storage.save_message(conv_id, user_msg)
        history.append(user_msg)

        # 执行智能体，流式输出
        tracker.reset()
        tools_used: list[str] = []
        new_msgs = []
        for chunk in agent.stream({"messages": history}, stream_mode="updates"):
            for node_name, value in chunk.items():
                if not isinstance(value, dict) or "messages" not in value:
                    continue
                for msg in value["messages"]:
                    new_msgs.append(msg)

                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        for tc in msg.tool_calls:
                            tools_used.append(tc["name"])
                    elif isinstance(msg, AIMessage) and msg.content:
                        # 先打印汇总行，再打印回复
                        stats = tracker.get_stats()
                        if stats["calls"] > 0:
                            tools_str = ", ".join(dict.fromkeys(tools_used))
                            print(f"\n  📊 Token: {stats['total_tokens']} "
                                  f"(入{stats['input_tokens']}/出{stats['output_tokens']}) "
                                  f"| {stats['calls']}次LLM调用")
                            if tools_str:
                                print(f"  🔧 工具: {tools_str}")
                        print(f"\n  🤖 {msg.content}\n")

        # 保存本轮消息到 DB + 追加到 history
        if new_msgs:
            storage.save_messages(conv_id, new_msgs)
            history.extend(new_msgs)

        # ── 检查是否需要压缩历史（按 token 占比） ──
        if estimate_tokens(history) > _COMPRESSION_AT:
            print(f"\n  📊 历史消息已达 {estimate_tokens(history):,} / {_CONTEXT_WINDOW:,} tokens，"
                  f"触发压缩...")
            history = compress_history(history, summary_llm)
            # 整替换：DB 镜像到压缩后的内存状态，避免旧消息残留
            storage.replace_all_messages(conv_id, history)


# ════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════

if __name__ == "__main__":
    run_chat()
