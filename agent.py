"""
ReAct 智能体 v3 — 模型实例化 + 图构建

用法：
  from agent import build_agent
  agent = build_agent()
  result = agent.invoke({"messages": [("human", "你好")]})
"""

import logging_config  # 初始化日志系统（必须在其他 import 之前）

import json
import logging
import time

import httpx
from langgraph.graph import StateGraph, END, MessagesState
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from typing import Literal

from config import config
from memory.history import estimate_tokens, prepare_history_for_agent
from tools import TOOLS
from tools.core.concurrency import concurrent_tool_node
from tools.core.token_tracker import TokenTracker

# 全局追踪器实例，供外部读取统计
tracker = TokenTracker()
from prompts import build_react_system_prompt


# ════════════════════════════════════════════════
# 1. 初始化 LLM + 绑定全部工具
# ════════════════════════════════════════════════

def _client_api_key() -> str:
    """校验启动所需配置，并兼容不校验密钥的本地 OpenAI 接口。"""
    if not config.model:
        raise ValueError("未配置 LLM_MODEL，请先运行 `a-stock setup`")
    if not config.base_url:
        raise ValueError("未配置 LLM_BASE_URL，请先运行 `a-stock setup`")
    if config.api_key:
        return config.api_key
    if config.provider_name == "ollama":
        return "ollama"
    raise ValueError("未配置 LLM_API_KEY，请先运行 `a-stock setup`")


llm = ChatOpenAI(
    model=config.model,
    api_key=_client_api_key(),
    base_url=config.base_url,
    temperature=config.temperature,
    max_tokens=config.max_tokens,
    timeout=config.request_timeout,
    max_retries=0,  # 统一由下方 ReAct 节点重试，避免两层重试成倍放大
    callbacks=[tracker],
)

react_llm = llm.bind_tools(TOOLS)
# 工具定义随每次请求发送，同样占用窗口；额外留出消息封装的估算余量。
_TOOL_TOKEN_RESERVE = estimate_tokens([
    SystemMessage(content=json.dumps(react_llm.kwargs.get("tools", []), ensure_ascii=False))
]) + 512
logger = logging.getLogger(__name__)


class LLMServiceUnavailableError(RuntimeError):
    """模型服务经过自动重试后仍不可用；供 CLI 和 Web 显示简短提示。"""


# ════════════════════════════════════════════════
# 2. ReAct 节点
# ════════════════════════════════════════════════

def _should_retry(error: Exception) -> bool:
    """判断是否为临时错误，同时检查 SDK 包装前后的整条异常链。"""
    current: BaseException | None = error
    seen = set()
    messages = []

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current).lower())

        # 这次故障最外层是 APIConnectionError，内部才是 httpx.ConnectError。
        # 按异常类型判断比只匹配最终的“Connection error.”文字更可靠。
        if isinstance(
            current,
            (APIConnectionError, APITimeoutError, RateLimitError, httpx.TransportError),
        ):
            return True

        if isinstance(current, APIStatusError):
            status_code = getattr(current, "status_code", 0)
            if status_code == 429 or 500 <= status_code < 600:
                return True

        current = current.__cause__ or current.__context__

    # 为兼容不同 OpenAI 兼容服务的自定义异常，保留文字判断作为兜底。
    msg = " ".join(messages)
    return any(kw in msg for kw in (
        "503", "502", "504", "429",
        "service_unavailable", "too busy",
        "rate_limit", "rate limit",
        "timeout", "timed out", "connection error",
        "connection reset", "connection refused",
        "unexpected_eof", "unexpected eof", "eof occurred",
    ))


def _call_model(state: MessagesState, *, finalize: bool = False) -> dict:
    """调用 LLM 思考（带指数退避重试）"""
    # 每次工具返回后都会再次进入此节点，因此每次都检查，而非只检查用户输入。
    # 仅压缩本次请求的副本：图中保留完整记录，避免流式界面重复显示历史消息，
    # 也避免数据库漏存本轮工具结果。持久化压缩仍由会话入口统一完成。
    request_history, _ = prepare_history_for_agent(
        state["messages"], summary_llm,
        compression_at=max(1, int(config.context_window * config.compression_ratio) - _TOOL_TOKEN_RESERVE),
        context_window=config.context_window,
        response_reserve=config.max_tokens + _TOOL_TOKEN_RESERVE,
    )
    max_retries = config.max_retries
    if finalize:
        # 达到本轮工具轮数上限后，用不绑定工具的模型调用收尾，避免耗尽图步数。
        request_history = [*request_history, SystemMessage(content=(
            "本轮工具调用次数已达到上限。现在根据已返回的信息直接回答用户，不再调用工具。"
            "缺失、失败或过期的数据要明确说明，不得编造；资料不足时给出有限结论。"
        ))]
    for attempt in range(max_retries + 1):
        try:
            response = (llm if finalize else react_llm).invoke(request_history)
            return {"messages": [response]}
        except Exception as e:
            retryable = _should_retry(e)
            if retryable and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "LLM 调用失败（%s），%ds 后重试 (%d/%d)",
                    type(e).__name__, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
            elif retryable:
                # 完整异常仍写入 DEBUG 日志文件；交互界面只显示简短中文提示。
                logger.debug("LLM 临时错误重试后仍失败", exc_info=True)
                raise LLMServiceUnavailableError(
                    f"模型服务连接失败，已自动重试 {max_retries} 次。"
                    "请检查网络或代理后重新发送。"
                ) from None
            else:
                raise


def _should_continue(state: MessagesState) -> Literal["tools", END]:
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return END


# ════════════════════════════════════════════════
# 3. 工具节点
# ════════════════════════════════════════════════

tool_node = concurrent_tool_node(TOOLS)


# ════════════════════════════════════════════════
# 4. 构建图
# ════════════════════════════════════════════════

def _call_bounded_model(state: MessagesState) -> dict:
    """为每轮聊天限制工具轮数，并预留一次不带工具的最终回答。"""
    messages = state["messages"]
    # 历史对话也保存在 state 中，只统计最近一条用户消息之后的工具调用，
    # 避免旧对话的调用次数占用当前轮的额度。
    turn_start = max(
        (index for index, message in enumerate(messages)
         if getattr(message, "type", None) in ("human", "user")),
        default=-1,
    )
    rounds = sum(
        bool(getattr(message, "tool_calls", None))
        for message in messages[turn_start + 1:]
    )
    # 每个工具轮占模型和工具两个节点；图上限内预留一个最终回答节点。
    max_tool_rounds = max(1, (config.max_agent_steps - 1) // 2)
    return _call_model(state, finalize=rounds >= max_tool_rounds)


def build_agent():
    workflow = StateGraph(MessagesState)
    workflow.add_node("llm", _call_bounded_model)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("llm")
    workflow.add_conditional_edges(
        "llm", _should_continue, {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "llm")
    return workflow.compile()


# ════════════════════════════════════════════════
# 5. 便捷接口
# ════════════════════════════════════════════════

agent = build_agent()

# LangGraph 的 recursion_limit 统计图节点步数；每轮工具调用占模型和工具两个节点，
# 最后还要留一个模型节点生成答复，避免工具轮数用完后再次触发递归上限。
AGENT_RUN_CONFIG = {"recursion_limit": config.max_agent_steps}


# 供 chat.py 调用的无绑定 LLM（用于对话摘要）
summary_llm = llm


def ask(message: str) -> str:
    """单轮问答（注入 system prompt 后执行）"""
    result = agent.invoke({
        "messages": [
            SystemMessage(content=build_react_system_prompt(
                role="analyst",
                include_rules=["base", "risk", "stock_resolve"],
                include_skills=["trend", "indicator", "volume"],
            )),
            ("human", message),
        ]
    }, config=AGENT_RUN_CONFIG)
    return result["messages"][-1].content


# ════════════════════════════════════════════════
# 6. 命令行入口（快捷单轮问答）
# ════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:])))
    else:
        print("用法: python agent.py \"你的问题\"")
