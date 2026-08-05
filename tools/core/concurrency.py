"""
工具并发调度 — 基于依赖图的分层执行

不再使用硬编码的 NON_CONCURRENT_TOOLS 列表。
改为从每个工具的 docstring 中解析 Dependencies: [tool_a, tool_b] 声明，
自动构建依赖图，按拓扑排序分层执行。

同一层内的工具无依赖关系 → 并发执行
不同层之间有依赖关系 → 串行执行

用法：
  from tools.concurrency import concurrent_tool_node
  tool_node = concurrent_tool_node(TOOLS)
"""

import re
import time
import logging
import concurrent.futures
from langchain_core.messages import ToolMessage

from tools.core.error_handler import (
    execute_with_error_handling,
    format_error_for_llm,
    ErrorCategory,
    ToolError,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════
# 依赖图构建
# ════════════════════════════════════════════════

def _parse_dependencies(tool) -> list[str]:
    """
    从工具函数的 docstring 中解析依赖声明。

    docstring 格式示例：
        def get_realtime_quote(...):
            \"\"\"
            获取实时行情

            Dependencies: [complete_stock_info]

            参数:
            ...
            \"\"\"

    返回：
        ["complete_stock_info"]
    """
    # StructuredTool 把原始函数的 __doc__ 放在 .description 中
    doc = tool.description or ""
    match = re.search(r"Dependencies:\s*\[([^\]]*)\]", doc)
    if not match:
        return []
    content = match.group(1).strip()
    if not content:
        return []
    return [name.strip() for name in content.split(",")]


def _build_dep_graph(tools: list) -> dict[str, list[str]]:
    """
    遍历所有工具，构建完整的依赖图。

    返回：
        {
            "get_realtime_quote": ["complete_stock_info"],
            "get_fundamental_info": ["complete_stock_info"],
            ...
        }
        未声明依赖的工具不会出现在图中（等价于空列表）。
    """
    graph = {}
    for t in tools:
        deps = _parse_dependencies(t)
        if deps:
            graph[t.name] = deps
    return graph


# ════════════════════════════════════════════════
# 分层调度
# ════════════════════════════════════════════════

def _schedule_layers(
    tool_calls: list,
    dep_graph: dict[str, list[str]],
) -> list[list]:
    """
    对本次调用的工具集合做拓扑排序，分成若干层。

    使用 tool_call 的唯一 ID 而非工具名作为 key，
    避免同名工具（如两次 load_skill）被覆盖。

    算法：
        1. 筛选依赖图中仅涉及本次调用工具的子图
        2. 反复移除"所有依赖项已被满足"的工具
        3. 每轮移除的工具归为一层

    返回：
        [[layer0_tc1, layer0_tc2], [layer1_tc1], ...]
        同一层内可并发，层间必须串行。
    """
    # 用唯一 ID 索引每个 tool_call
    id_to_tc = {tc["id"]: tc for tc in tool_calls}
    all_ids = set(id_to_tc.keys())

    # name → [id1, id2, ...]（支持同名工具如 load_skill x2）
    name_to_ids: dict[str, set[str]] = {}
    for tc in tool_calls:
        name_to_ids.setdefault(tc["name"], set()).add(tc["id"])

    # 每个 tool_call 的本地依赖（仅限本批调用中的工具）
    local_deps: dict[str, set[str]] = {}
    for tc_id, tc in id_to_tc.items():
        dep_names = dep_graph.get(tc["name"], [])
        dep_ids = set()
        for dname in dep_names:
            if dname in name_to_ids:
                dep_ids.update(name_to_ids[dname])
        # 排除自身，仅保留也在本批中的依赖
        local_deps[tc_id] = {d for d in dep_ids if d in all_ids and d != tc_id}

    remaining = set(all_ids)
    layers = []

    while remaining:
        current = set()
        for tc_id in remaining:
            if not (local_deps.get(tc_id, set()) & remaining):
                current.add(tc_id)

        if not current:
            current = remaining.copy()

        layers.append([id_to_tc[tid] for tid in current])
        remaining -= current

    return layers


# ════════════════════════════════════════════════
# 工具执行节点
# ════════════════════════════════════════════════

def concurrent_tool_node(tools: list):
    """
    创建一个基于依赖图分层调度的工具执行节点。

    特点：
      - 自动从 docstring 解析 Dependencies 构建依赖图
      - 同一层内完全并发
      - 层间串行等待（前一层所有工具执行完毕再启动下一层）
      - 每个工具有 30s 超时保护
    """
    tool_map = {t.name: t for t in tools}
    dep_graph = _build_dep_graph(tools)

    # ── 打印依赖图信息 ──────────────────────────
    if dep_graph:
        logger.debug("已构建 %d 个工具依赖关系", len(dep_graph))
        for name, deps in dep_graph.items():
            logger.debug("  %s → %s", name, deps)

    # ── 单个工具执行 ─────────────────────────────

    def _execute_tool(tc, timeout: int = 30) -> ToolMessage:
        tool = tool_map.get(tc["name"])
        if not tool:
            return ToolMessage(
                content=format_error_for_llm(
                    ToolError(
                        category=ErrorCategory.ARGUMENT,
                        message=f"未找到工具: {tc['name']}",
                        tool_name=tc["name"],
                        tool_args=tc["args"],
                        tool_call_id=tc["id"],
                    )
                ),
                tool_call_id=tc["id"],
            )

        def _run() -> ToolMessage:
            success, content, error = execute_with_error_handling(
                tool_func=tool,
                tool_name=tc["name"],
                tool_args=tc["args"],
                tool_call_id=tc["id"],
            )
            if success:
                return ToolMessage(content=content, tool_call_id=tc["id"])
            else:
                return ToolMessage(
                    content=format_error_for_llm(error),
                    tool_call_id=tc["id"],
                )

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_run)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning("%s 超过 %ds 无响应", tc['name'], timeout)
            return ToolMessage(
                content=format_error_for_llm(
                    ToolError(
                        category=ErrorCategory.RETRYABLE,
                        message=f"工具 {tc['name']} 执行超时（{timeout}s），服务器无响应",
                        tool_name=tc["name"],
                        tool_args=tc["args"],
                        tool_call_id=tc["id"],
                    )
                ),
                tool_call_id=tc["id"],
            )
        finally:
            pool.shutdown(wait=False)

    # ── 主节点函数 ───────────────────────────────

    def _node(state) -> dict:
        last_msg = state["messages"][-1]
        tool_calls = last_msg.tool_calls

        if not tool_calls:
            return {"messages": []}

        start = time.time()
        total = len(tool_calls)

        # 分层调度
        layers = _schedule_layers(tool_calls, dep_graph)
        num_layers = len(layers)

        if num_layers == 1 and len(layers[0]) == 1:
            # 只有一个工具，无需调度逻辑
            tc = layers[0][0]
            result_msg = _execute_tool(tc)
            elapsed = time.time() - start
            logger.debug("执行 %s (%.1fs)", tc['name'], elapsed)
            return {"messages": [result_msg]}

        logger.debug("%d 个工具分 %d 层执行", total, num_layers)

        all_results = []
        for layer_idx, layer_tcs in enumerate(layers):
            n = len(layer_tcs)
            if n == 1:
                # 单工具，直接执行（不创建线程池）
                tc = layer_tcs[0]
                result_msg = _execute_tool(tc)
                all_results.append(result_msg)
                logger.debug("层%d %s 完成", layer_idx, tc['name'])
            else:
                # 多工具，并发执行
                logger.debug("层%d %d 个工具并发: %s", layer_idx, n,
                             [tc['name'] for tc in layer_tcs])
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(n, 5)
                ) as executor:
                    future_map = {
                        executor.submit(_execute_tool, tc): tc
                        for tc in layer_tcs
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        all_results.append(future.result())
                logger.debug("层%d 并发完成", layer_idx)

        elapsed = time.time() - start
        logger.debug("全部完成 (%.1fs)", elapsed)
        return {"messages": all_results}

    return _node
