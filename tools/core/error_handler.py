"""
工具错误处理 - 分类处理工具调用过程中的错误

错误分类：
  1. 参数错误（ArgumentError）— 参数格式不对/缺失
     处理：将错误信息连同原始 tool_call 送回 LLM，让它自己修正参数

  2. 临时服务错误（RetryableError）— 网络波动、风控临时拦截
     处理：指数退避重试（最多 3 次，间隔 1s/2s/4s）

  3. 永久性错误（FatalError）— 工具本身的问题、API key 无效、数据源永久不可用
     处理：立即终止，报告错误
"""

import time
import logging
import traceback
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    ARGUMENT = "argument"       # 参数错误，可让 LLM 修正
    RETRYABLE = "retryable"     # 临时服务错误，可重试
    FATAL = "fatal"             # 永久性错误，立即终止


@dataclass
class ToolError:
    """工具执行错误的封装"""
    category: ErrorCategory
    message: str
    tool_name: str
    tool_args: dict
    tool_call_id: str
    original_exception: Optional[Exception] = None
    traceback_str: str = ""


def classify_error(e: Exception, tool_name: str, tool_args: dict) -> ErrorCategory:
    """
    判断错误属于哪一类。

    参数错误（ArgumentError）：
      - ValueError（代码不合法、日期格式不对等）
      - KeyError（返回数据中缺少关键字段）
      - 用户主动抛出的 ValueError

    临时服务错误（RetryableError）：
      - ConnectionError / ConnectionAbortedError（网络波动、风控断开）
      - TimeoutError / Timeout（请求超时）
      - RemoteDisconnected（服务器主动断开）
      - 任何以 "Remote end closed" 开头的错误

    永久性错误（FatalError）：
      - API key 无效（AuthenticationError / Unauthorized）
      - 工具代码本身的 bug（AttributeError、TypeError 等代码错误）
      - 数据源返回明确的"不可用"状态
    """
    error_str = str(e).lower()
    error_type = type(e).__name__

    # ── 参数错误 ──
    if isinstance(e, ValueError):
        return ErrorCategory.ARGUMENT

    # KeyError 多数来自上游数据字段变化或工具实现，不是用户修改参数能解决的问题。
    if isinstance(e, KeyError):
        return ErrorCategory.FATAL

    if "invalid" in error_str and ("code" in error_str or "param" in error_str):
        return ErrorCategory.ARGUMENT

    if "must be" in error_str and "digit" in error_str:
        return ErrorCategory.ARGUMENT

    # ── 临时服务错误 ──
    if isinstance(e, ConnectionError):
        return ErrorCategory.RETRYABLE

    if "remote end closed" in error_str or "connection aborted" in error_str:
        return ErrorCategory.RETRYABLE

    if "timeout" in error_type.lower() or "timeout" in error_str:
        return ErrorCategory.RETRYABLE

    if "reset" in error_str or "refused" in error_str or "eof" in error_str:
        return ErrorCategory.RETRYABLE

    # ── 永久性错误 ──
    if isinstance(e, (AttributeError, TypeError, ImportError, ModuleNotFoundError)):
        return ErrorCategory.FATAL

    if "api_key" in error_str or "unauthorized" in error_str or "authentication" in error_str:
        return ErrorCategory.FATAL

    if "not found" in error_str and "stock" in error_str:
        return ErrorCategory.ARGUMENT

    # 未识别异常通常是工具实现或上游数据结构变化，重复调用只会浪费时间。
    return ErrorCategory.FATAL


def execute_with_error_handling(
    tool_func: Any,
    tool_name: str,
    tool_args: dict,
    tool_call_id: str,
    max_retries: int = 3,
) -> tuple[bool, Optional[str], Optional[ToolError]]:
    """
    执行工具并处理错误。

    参数:
        tool_func: 工具函数（有 .invoke 方法）
        tool_name: 工具名称
        tool_args: 工具参数
        tool_call_id: 工具调用 ID
        max_retries: 最大重试次数（仅对 RETRYABLE 有效）

    返回:
        (success, result_content, error_info)
        success=True  → result_content 是执行结果
        success=False → error_info 是封装的错误信息
    """
    # ── 第1步：尝试执行 ──
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = tool_func.invoke(tool_args)
            # 检查结果是否为空或错误
            result_str = _serialize(result)
            return (True, result_str, None)

        except Exception as e:
            last_error = e
            category = classify_error(e, tool_name, tool_args)

            # 参数错误 → 不重试，立即返回
            if category == ErrorCategory.ARGUMENT:
                return (False, None, ToolError(
                    category=ErrorCategory.ARGUMENT,
                    message=str(e),
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_call_id=tool_call_id,
                    original_exception=e,
                    traceback_str=traceback.format_exc(),
                ))

            # 永久性错误 → 不重试，立即终止
            if category == ErrorCategory.FATAL:
                return (False, None, ToolError(
                    category=ErrorCategory.FATAL,
                    message=str(e),
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_call_id=tool_call_id,
                    original_exception=e,
                    traceback_str=traceback.format_exc(),
                ))

            # 临时错误 → 指数退避重试
            if attempt < max_retries:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning("重试 %s 第 %d 次失败 (%s), %ds 后重试... (%r)",
                               tool_name, attempt + 1, category.value, wait, e)
                time.sleep(wait)
                continue

    # ── 第2步：所有重试都失败了 ──
    return (False, None, ToolError(
        category=ErrorCategory.RETRYABLE,
        message=f"重试 {max_retries} 次后仍然失败: {last_error}",
        tool_name=tool_name,
        tool_args=tool_args,
        tool_call_id=tool_call_id,
        original_exception=last_error,
        traceback_str=traceback.format_exc(),
    ))


def format_error_for_llm(error: ToolError) -> str:
    """
    将错误格式化为 LLM 能理解的消息。

    三种错误格式不同：
      1. 参数错误 → 告诉 LLM 参数哪里不对，让它修正
      2. 临时错误 → 告诉 LLM 服务暂时不可用，可以稍后重试或跳过
      3. 永久错误 → 告诉 LLM 该工具不可用，不要再调了
    """
    if error.category == ErrorCategory.ARGUMENT:
        return (
            f"[工具错误-参数] 调用 {error.tool_name} 时参数不正确。\n"
            f"参数: {error.tool_args}\n"
            f"错误: {error.message}\n"
            f"请修正参数后重新调用。"
        )

    elif error.category == ErrorCategory.RETRYABLE:
        return (
            f"[工具错误-临时] 调用 {error.tool_name} 时服务暂时不可用。\n"
            f"参数: {error.tool_args}\n"
            f"错误: {error.message}\n"
            f"可以稍后重试，或者暂时跳过这个数据继续分析。"
        )

    elif error.category == ErrorCategory.FATAL:
        return (
            f"[工具错误-永久] 调用 {error.tool_name} 时发生不可恢复的错误。\n"
            f"参数: {error.tool_args}\n"
            f"错误: {error.message}\n"
            f"该工具无法继续使用，建议不再调用它。"
        )

    return f"[工具错误] {error.tool_name} 执行失败: {error.message}"


def _serialize(result) -> str:
    """将结果序列化为字符串"""
    # DataFrame 优先用 to_string（可读表格，且避免 date 等类型 json 序列化失败）
    if hasattr(result, "to_string"):
        return result.to_string()
    if hasattr(result, "to_dict"):
        import json
        return json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    if hasattr(result, "to_json"):
        return str(result.to_json())
    return str(result)
