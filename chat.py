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

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from agent import AGENT_RUN_CONFIG, LLMServiceUnavailableError, agent, summary_llm, tracker
from cli_ui import NEW_CONVERSATION, TerminalUI
from config import config
from memory import ChatStorage, compress_history, prepare_history_for_agent
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

def _create_conversation(storage: ChatStorage) -> str:
    """创建会话并立即写入系统提示词，保证第一轮对话也有完整上下文。"""
    conv_id = storage.create_conversation()
    _init_system_prompt(storage, conv_id)
    return conv_id


def _pick_or_create_conversation(storage: ChatStorage, ui: TerminalUI) -> str | None:
    """启动时用弹窗恢复已有会话；取消选择则退出聊天。"""
    convs = storage.list_conversations()
    if not convs:
        conv_id = _create_conversation(storage)
        ui.update_conversation("新对话")
        print("\n📌 新对话已创建\n")
        return conv_id

    selected = ui.choose_conversation(
        convs,
        title="选择对话",
        allow_new=True,
    )
    if selected is None:
        return None
    if selected == NEW_CONVERSATION:
        conv_id = _create_conversation(storage)
        ui.update_conversation("新对话")
        print("\n📌 新对话已创建\n")
        return conv_id

    conversation = next(c for c in convs if c["id"] == selected)
    ui.update_conversation(conversation["title"])
    print(f"\n📂 已恢复对话：{conversation['title']}\n")
    conv_id = conversation["id"]
    return conv_id


def run_chat():
    """终端连续对话 + SQLite 持久化 + 自动历史压缩。"""
    storage = ChatStorage()

    # TerminalUI 统一管理输入、补全、快捷键和选择窗口；chat.py 只保留业务流程。
    hist_file = os.path.join(os.path.dirname(__file__), "data", ".chat_history")
    ui = TerminalUI(history_file=hist_file, model_name=config.model)

    print("\n📊 A 股分析助手")
    print("  输入 / 可查看命令，输入 /help 查看完整帮助。")

    # 选择对话
    conv_id = _pick_or_create_conversation(storage, ui)
    if conv_id is None:
        print("\n👋 已取消\n")
        return

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
            user_input = ui.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 已退出")
            break

        # ── 命令处理 ──────────────────────────
        if user_input.lower() in ("exit", "quit", "q", "/quit"):
            break

        if user_input.lower() in ("/help", "/?"):
            print(ui.help_text())
            continue

        if user_input.lower() == "/new":
            conv_id = _create_conversation(storage)
            # 重新从数据库加载，把刚写入的 SystemMessage 一并交给 Agent。
            history = storage.get_messages(conv_id)
            needs_title = True
            ui.update_conversation("新对话")
            print("\n📌 已切换到新对话\n")
            continue

        if user_input.lower() == "/list":
            convs = storage.list_conversations()
            if not convs:
                print("  (暂无保存的对话)\n")
                continue
            selected = ui.choose_conversation(
                convs,
                title="切换对话",
                current_id=conv_id,
                allow_new=True,
            )
            if selected == NEW_CONVERSATION:
                conv_id = _create_conversation(storage)
                history = storage.get_messages(conv_id)
                needs_title = True
                ui.update_conversation("新对话")
                print("\n📌 已切换到新对话\n")
            elif selected and selected != conv_id:
                conversation = next(c for c in convs if c["id"] == selected)
                conv_id = selected
                history = storage.get_messages(conv_id)
                needs_title = conversation["title"] == "新对话"
                ui.update_conversation(conversation["title"])
                print(f"\n📂 已切换到：{conversation['title']} ({len(history)} 条消息)\n")
            continue

        if user_input.lower().startswith("/delete"):
            convs = storage.list_conversations()
            if not convs:
                print("  (暂无保存的对话)\n")
                continue
            target_id = ui.choose_conversation(
                convs,
                title="删除对话",
                current_id=conv_id,
            )
            if target_id:
                target = next(c for c in convs if c["id"] == target_id)
                if ui.confirm_delete(target["title"]):
                    storage.delete_conversation(target_id)
                    print(f"\n🗑 已删除：{target['title']}\n")
                    if target_id == conv_id:
                        conv_id = _create_conversation(storage)
                        history = storage.get_messages(conv_id)
                        needs_title = True
                        ui.update_conversation("新对话")
                        print("📌 已自动创建新对话\n")
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
                ui.update_conversation(title)
                print(f"  对话已重命名为: {title}\n")
            else:
                print("  用法: /title <对话名称>\n")
            continue

        # 以 / 开头但未识别的内容不发送给模型，避免误操作产生一次 API 请求。
        if user_input.startswith("/"):
            print(f"  未知命令：{user_input.split()[0]}。输入 /help 查看可用命令。\n")
            continue

        if not user_input:
            continue

        # ── 正常对话流程 ──────────────────────────
        # 输入控件提交后会被清除，这里只回显真正发给 Agent 的聊天内容。
        # /list 等界面命令在前面已经处理，因此不会作为聊天记录重复显示。
        ui.echo_user_message(user_input)

        # 先只构造候选历史。等 Agent 完整成功后再原子写入数据库，
        # 避免网络或模型异常留下只有 HumanMessage 的半轮对话。
        user_msg = HumanMessage(content=user_input)
        candidate_history = [*history, user_msg]

        tracker.reset()
        tools_used: list[str] = []
        new_msgs = []
        try:
            # 压缩必须发生在请求前；发生压缩时稍后用整表替换同步数据库镜像。
            prepared_history, was_compressed = prepare_history_for_agent(
                candidate_history,
                summary_llm,
                compression_at=int(_COMPRESSION_AT),
                context_window=_CONTEXT_WINDOW,
                response_reserve=config.max_tokens,
            )

            for chunk in agent.stream(
                {"messages": prepared_history},
                config=AGENT_RUN_CONFIG,
                stream_mode="updates",
            ):
                for node_name, value in chunk.items():
                    if not isinstance(value, dict) or "messages" not in value:
                        continue
                    for msg in value["messages"]:
                        new_msgs.append(msg)

                        if isinstance(msg, AIMessage) and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tools_used.append(tc["name"])
                        elif isinstance(msg, AIMessage) and msg.content:
                            stats = tracker.get_stats()
                            if stats["calls"] > 0:
                                tools_str = ", ".join(dict.fromkeys(tools_used))
                                print(f"\n  📊 Token: {stats['total_tokens']} "
                                      f"(入{stats['input_tokens']}/出{stats['output_tokens']}) "
                                      f"| {stats['calls']}次LLM调用")
                                if tools_str:
                                    print(f"  🔧 工具: {tools_str}")
                            print(f"\n  🤖 {msg.content}\n")

            # 没有最终回答不算成功，不能把不完整的工具链写进历史。
            has_final_answer = any(
                isinstance(msg, AIMessage) and msg.content and not msg.tool_calls
                for msg in new_msgs
            )
            if not has_final_answer:
                raise RuntimeError("Agent 未返回最终回答，请重试")

            complete_history = [*prepared_history, *new_msgs]
            if was_compressed:
                storage.replace_all_messages(conv_id, complete_history)
            else:
                storage.save_messages(conv_id, [user_msg, *new_msgs])
            history = complete_history

            # 标题也在本轮成功后更新，失败重试时仍保留“新对话”状态。
            if needs_title:
                title = user_input[:30] + ("..." if len(user_input) > 30 else "")
                storage.update_title(conv_id, title)
                needs_title = False
                ui.update_conversation(title)
        except LLMServiceUnavailableError as exc:
            # 可恢复网络错误已经在 Agent 内重试，这里不再向终端打印整页堆栈。
            logger.error("本轮对话执行失败: %s", exc)
            print(f"\n  ❌ 本轮未保存：{exc}\n")
        except Exception as exc:
            logger.exception("本轮对话执行失败")
            print(f"\n  ❌ 本轮未保存：{exc}\n")


# ════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════

if __name__ == "__main__":
    run_chat()
