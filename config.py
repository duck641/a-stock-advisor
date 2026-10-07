"""LLM 配置 - 内置主流厂商预设，自动补全参数

核心设计：用户只需填 provider + api_key，model / base_url / context_window
自动从预设补齐。想手动覆盖就显式填对应字段。

用法:
  # 只需 2 行
  LLM_PROVIDER=deepseek
  LLM_API_KEY=***

  # 换成 OpenAI？改 1 行
  LLM_PROVIDER=openai

  # 换成本地 Ollama？API key 都不用填
  LLM_PROVIDER=ollama

  # 高级：手动覆盖预设
  LLM_PROVIDER=deepseek
  LLM_MODEL=deepseek-reasoner        # 覆盖默认模型
  LLM_TEMPERATURE=0.1               # 覆盖默认温度
"""

import os
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ═══════════════════════════════════════════════════════════════
# 厂商预设表
#
# 新增厂商只需在这里加一行，无需改其他代码。
# - default_model: provider 未指定 model 时使用
# - base_url:      provider 未指定 base_url 时使用
# - context_window: provider 未指定 context_window 时使用
# ═══════════════════════════════════════════════════════════════

PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {
        "default_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com/v1",
        "context_window": 1_000_000,
        "default_temperature": 0.2,
    },
    "openai": {
        "default_model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "context_window": 128000,
    },
    "qwen": {
        "default_model": "qwen-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "context_window": 131072,
    },
    "glm": {
        "default_model": "glm-4-plus",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "context_window": 128000,
    },
    "moonshot": {
        "default_model": "moonshot-v1-8k",
        "base_url": "https://api.moonshot.cn/v1",
        "context_window": 8192,
    },
    "ollama": {
        "default_model": "qwen2.5:7b",
        "base_url": "http://localhost:11434/v1",
        "context_window": 32768,
    },
}


class LLMConfig(BaseSettings):
    """通用 LLM 配置，支持主流厂商预设 + 手动覆盖。

    字段说明：
      provider      — 厂商标识（deepseek / openai / qwen / glm / moonshot / ollama）
                      填了则自动补全 model / base_url / context_window。
                      留空则完全手动配置（兼容旧版 .env）。
      model         — 模型名称。留空则用 provider 的默认模型。
      api_key       — API 密钥（ollama 不需要）。
      base_url      — API 端点。留空则用 provider 的默认端点。
      temperature   — 生成温度，股票分析默认 0.2。
      max_tokens    — 单次最大输出 token 数，默认 4096。
      context_window — 模型上下文窗口大小，0 则用 provider 默认值。
      compression_ratio — token 占比阈值，触发历史压缩（默认 0.6）。
    """

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), ".env"),
        env_file_encoding="utf-8",
        env_prefix="LLM_",
        extra="ignore",
    )

    # ── 连接配置 ─────────────────────────────────────
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""

    # ── 行为参数 ─────────────────────────────────────
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=4096, gt=0)
    request_timeout: float = Field(default=60, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    # LangGraph 图节点最大执行步数；工具轮数会为最终回答预留空间。
    max_agent_steps: int = Field(default=16, ge=4, le=100)

    # ── 上下文管理 ──────────────────────────────────
    context_window: int = Field(default=0, ge=0)
    compression_ratio: float = Field(default=0.6, gt=0, lt=1)

    @model_validator(mode="after")
    def _apply_provider_preset(self):
        """provider 不为空时，自动补全 model / base_url / context_window。

        规则：显式填了的保留，没填的从预设补齐。
        不进预设的 provider（如 custom）不报错，保持原值。
        """
        provider = self.provider.strip().lower()
        self.provider = provider

        # provider 为空 → 完全手动模式（兼容旧版 .env）
        if not provider:
            if not self.model:
                self.model = "deepseek-flash"
            if not self.base_url:
                self.base_url = "https://api.deepseek.com/v1"
            if not self.context_window:
                self.context_window = 1_000_000
            return self

        # provider 指定了但不在预设表中 → 用户可能用了自定义 provider 名
        # 不做补全，但确保 context_window 有兜底
        preset = PROVIDER_PRESETS.get(provider)
        if not preset:
            if not self.context_window:
                self.context_window = 65536
            return self

        # 自动补全
        if not self.model:
            self.model = preset["default_model"]
        if not self.base_url:
            self.base_url = preset["base_url"]
        if not self.context_window:
            self.context_window = preset["context_window"]

        return self

    @property
    def provider_name(self) -> str:
        """识别当前使用的厂商（预先设定或从 base_url 推断）"""
        # 先看 preset 里有没有
        if self.provider and self.provider in PROVIDER_PRESETS:
            return self.provider
        # 从 base_url 推断
        url = self.base_url.lower()
        if "deepseek" in url:
            return "deepseek"
        elif "openai" in url:
            return "openai"
        elif "dashscope" in url or "aliyun" in url:
            return "qwen"
        elif "bigmodel" in url:
            return "glm"
        elif "moonshot" in url:
            return "moonshot"
        elif "localhost" in url or "127.0.0.1" in url:
            return "ollama"
        return "unknown"


# 单例实例
config = LLMConfig()
