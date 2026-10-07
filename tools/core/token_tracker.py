"""
LLM 调用追踪器 — 基于 LangChain BaseCallbackHandler

记录每次 LLM 调用的 token 用量和耗时，支持按轮累积统计。

用法：创建 ChatOpenAI 时传入：
    llm = ChatOpenAI(..., callbacks=[TokenTracker()])
"""
import time
import logging
from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)


class TokenTracker(BaseCallbackHandler):
    """追踪 LLM 调用的 token 用量和耗时，支持按轮累积统计"""

    def __init__(self):
        self._start_times: dict[str, float] = {}
        self._models: dict[str, str] = {}
        # 本轮累积统计
        self._input_tokens = 0
        self._output_tokens = 0
        self._calls = 0
        self._elapsed_ms = 0

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        """LLM 调用开始时记录时间戳和模型名"""
        self._start_times[run_id] = time.time()
        try:
            self._models[run_id] = kwargs["invocation_params"]["model"]
        except (KeyError, TypeError):
            self._models[run_id] = serialized.get("kwargs", {}).get("model", "unknown")

    def on_llm_end(self, response, *, run_id, **kwargs):
        """LLM 调用结束时累积用量统计"""
        elapsed = (time.time() - self._start_times.pop(run_id, 0)) * 1000

        usage = response.llm_output.get("token_usage", {}) if response.llm_output else {}
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        model = self._models.pop(run_id, "unknown")

        # 累积
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._calls += 1
        self._elapsed_ms += elapsed

        logger.debug(
            "LLM 调用 | 模型: %s | 输入: %d tokens | 输出: %d tokens | 耗时: %.0fms",
            model, input_tokens, output_tokens, elapsed,
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        """记录单次 LLM 调用错误；是否重试由 Agent 统一决定。"""
        self._start_times.pop(run_id, None)
        self._models.pop(run_id, None)
        # 单次失败可能马上自动恢复，避免在终端提前显示成最终故障。
        logger.debug("单次 LLM 调用失败: %s", error)

    def get_stats(self) -> dict:
        """获取本轮累积统计"""
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self._input_tokens + self._output_tokens,
            "calls": self._calls,
            "elapsed_ms": round(self._elapsed_ms),
        }

    def reset(self):
        """重置本轮统计（新一轮对话开始前调用）"""
        self._input_tokens = 0
        self._output_tokens = 0
        self._calls = 0
        self._elapsed_ms = 0
