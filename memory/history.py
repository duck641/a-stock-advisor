"""
对话历史管理 — token 估算、按轮压缩

压缩策略：
  1. 以 HumanMessage 为边界将消息拆分为"对话轮次"
  2. 保留最近 KEEP_TURNS 轮完整对话
  3. 更早的轮次压缩为一条摘要（SystemMessage name='summary'）
  4. token 阈值兜底：如果保留的轮次仍超限，减少保留轮次
"""

import logging
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

logger = logging.getLogger(__name__)

# 压缩后保留的最近对话轮次
KEEP_TURNS = 3


def estimate_tokens(messages: list) -> int:
    """估算消息列表的总 token 数"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in messages:
            content = m.content or ""
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        total += len(enc.encode(block.get("text", "")))
            total += 3
        total += 2
        return total
    except Exception:
        total = 0
        for m in messages:
            content = m.content or ""
            if isinstance(content, str):
                for ch in content:
                    if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
                        total += 2
                    else:
                        total += 0.25
            total += 3
        return int(total) + 2


# ════════════════════════════════════════════════
# 对话轮次拆分
# ════════════════════════════════════════════════

def split_turns(messages: list) -> list[list]:
    """
    以 HumanMessage 为边界拆分对话轮次。

    SystemMessage 归属到紧随其后的第一轮。

    示例:
      [System, Human("问题1"), AI(tc), Tool, AI("答案")] → 一轮
      [Human("问题1"), AI("答案1"), Human("问题2"), AI("答案2")] → 两轮
    """
    turns = []
    current = []

    for m in messages:
        if isinstance(m, HumanMessage):
            if current:
                turns.append(current)
            current = [m]
        else:
            current.append(m)

    if current:
        turns.append(current)

    return turns


# ════════════════════════════════════════════════
# 按轮压缩
# ════════════════════════════════════════════════

def compress_history(
    history: list,
    summary_llm,
    keep_turns: int = KEEP_TURNS,
    max_tokens: int = 0,
) -> list:
    """
    按对话轮次压缩历史。

    参数:
        history:    完整消息列表
        summary_llm: 用于生成摘要的 LLM
        keep_turns: 保留最近 N 轮完整对话（默认 3）
        max_tokens:  token 上限兜底。如果 keep_turns 轮仍超限，
                     逐步减少保留轮次直到满足（最少保留 1 轮）

    返回:
        新的消息列表：[system_messages...] + [summary] + [保留的轮次消息]
    """
    if not history:
        return history

    turns = split_turns(history)

    # 分离 system-only 的轮次（如 system prompt），过滤掉旧摘要
    # 旧摘要内容已被新摘要吸收，保留会导致摘要累积
    system_turns = []
    real_turns = []
    for t in turns:
        if all(isinstance(m, SystemMessage) for m in t):
            # 过滤掉旧的摘要消息，只保留初始 system prompt
            filtered = [m for m in t if "前情摘要" not in (m.content or "")]
            if filtered:
                system_turns.append(filtered)
        else:
            real_turns.append(t)

    if len(real_turns) <= keep_turns:
        return history

    # 分离需要压缩的旧轮次和保留的最近轮次
    old_turns = real_turns[:-keep_turns]
    recent_turns = real_turns[-keep_turns:]

    # ── token 兜底：保留轮次仍超标则减少 ──
    if max_tokens > 0:
        for n in range(keep_turns - 1, 0, -1):
            recent_flat = [m for t in recent_turns[-n:] for m in t]
            sys_flat = [m for t in system_turns for m in t]
            if estimate_tokens(sys_flat + recent_flat) <= max_tokens:
                keep_turns = n
                old_turns = real_turns[:-n]
                recent_turns = real_turns[-n:]
                break

    # ── 构建摘要素材 ──
    summary_src = []
    for turn in old_turns:
        for m in turn:
            if isinstance(m, HumanMessage):
                summary_src.append(f"用户: {m.content[:200]}")
            elif isinstance(m, AIMessage) and m.content and not m.tool_calls:
                summary_src.append(f"助手: {m.content[:300]}")
            elif isinstance(m, SystemMessage) and "前情摘要" in (m.content or ""):
                summary_src.append(f"[历史摘要] {m.content}")

    if not summary_src:
        return history

    # ── 生成摘要 ──
    prompt = (
        "请将以下对话历史压缩为 3-5 句中文摘要，保留：\n"
        "- 用户关注的核心股票、板块\n"
        "- 已经得出的分析结论（看好/看空）\n"
        "- 重要数据（关键价格、指标）\n\n"
        "对话历史：\n"
        f"{chr(10).join(summary_src)}\n\n"
        "摘要："
    )

    try:
        resp = summary_llm.invoke([HumanMessage(content=prompt)])
        summary = resp.content.strip()
        logger.info("对话历史已压缩（%d 轮 → 摘要，保留最近 %d 轮）",
                     len(old_turns), len(recent_turns))

        # ── 拼接结果 ──
        result = []
        for t in system_turns:
            result.extend(t)
        result.append(SystemMessage(content=f"【前情摘要】{summary}", name="summary"))
        for t in recent_turns:
            result.extend(t)
        return result

    except Exception as e:
        logger.warning("压缩失败: %s，保留完整历史", e)
        return history


# ════════════════════════════════════════════════
# ConversationMemory（封装加载/压缩/保存）
# ════════════════════════════════════════════════

class ConversationMemory:

    def __init__(self, storage, summary_llm, context_window: int, compression_ratio: float):
        self.storage = storage
        self.summary_llm = summary_llm
        self.context_window = context_window
        self.compression_at = context_window * compression_ratio

    def load(self, conv_id: str) -> list:
        """从 DB 加载全部对话历史"""
        return self.storage.get_messages(conv_id)

    def before_send(self, history: list) -> list:
        """发送给 LLM 前检查 token 阈值，触发按轮压缩"""
        # 用 system 消息（提示词 + 摘要）的 token 加上真实对话的 token 来判断
        tokens = estimate_tokens(history)
        if tokens > self.compression_at:
            logger.info("触发压缩: %d / %d tokens", tokens, self.context_window)
            history = compress_history(
                history, self.summary_llm,
                max_turns=int(self.compression_at * 0.5),  # 兜底上限
            )
        return history

    def after_turn(self, conv_id: str, history: list, new_msgs: list):
        """对话结束后：保存新消息，持久化摘要"""
        if new_msgs:
            self.storage.save_messages(conv_id, new_msgs)
            history.extend(new_msgs)

        for m in history:
            if isinstance(m, SystemMessage) and "前情摘要" in (m.content or "") and m.name == "summary":
                self.storage.save_message(conv_id, m)
                break

    def save_summary(self, conv_id: str, summary: str):
        self.storage.save_message(
            conv_id,
            SystemMessage(content=f"【前情摘要】{summary}", name="summary"),
        )
