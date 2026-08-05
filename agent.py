"""
ReAct 智能体 v3 — 模型实例化 + 图构建

用法：
  from agent import build_agent
  agent = build_agent()
  result = agent.invoke({"messages": [("human", "你好")]})
"""

import logging_config  # 初始化日志系统（必须在其他 import 之前）

import logging
import time

from langgraph.graph import StateGraph, END, MessagesState
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from typing import Literal

from config import config
from tools import TOOLS
from tools.core.concurrency import concurrent_tool_node
from tools.core.token_tracker import TokenTracker

# 全局追踪器实例，供外部读取统计
tracker = TokenTracker()
from prompts import build_react_system_prompt


# ════════════════════════════════════════════════
# 1. 初始化 LLM + 绑定全部工具
# ════════════════════════════════════════════════

llm = ChatOpenAI(
    model=config.model,
    api_key=config.api_key,
    base_url=config.base_url,
    temperature=config.temperature,
    max_tokens=config.max_tokens,
    callbacks=[tracker],
)

react_llm = llm.bind_tools(TOOLS)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# 2. ReAct 节点
# ════════════════════════════════════════════════

def _should_retry(error: Exception) -> bool:
    """判断是否为可重试的临时服务错误（502/503/504/限流/超时）"""
    msg = str(error).lower()
    return any(kw in msg for kw in (
        "503", "502", "504", "429",
        "service_unavailable", "too busy",
        "rate_limit", "rate limit",
        "timeout", "timed out",
        "connection reset", "connection refused",
    ))


def _call_model(state: MessagesState) -> dict:
    """调用 LLM 思考（带指数退避重试）"""
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            response = react_llm.invoke(state["messages"])
            return {"messages": [response]}
        except Exception as e:
            if _should_retry(e) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "LLM 调用 %s，%ds 后重试 (%d/%d)",
                    type(e).__name__, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
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

def build_agent():
    workflow = StateGraph(MessagesState)
    workflow.add_node("llm", _call_model)
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
    })
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
