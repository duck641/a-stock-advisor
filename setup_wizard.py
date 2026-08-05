"""
交互式配置向导 — 引导用户填写 .env 中的必要参数

用法:
  from setup_wizard import run_setup
  run_setup()

只需输入 provider + api_key，其余参数自动从预设补齐。
"""

import os
from pathlib import Path

from config import PROVIDER_PRESETS

# ── 厂商展示信息 ──────────────────────────────
_PROVIDER_DISPLAY = {
    "deepseek":  ("DeepSeek",             "api.deepseek.com"),
    "openai":    ("OpenAI",               "api.openai.com"),
    "qwen":      ("通义千问 (阿里云)",      "dashscope.aliyuncs.com"),
    "glm":       ("智谱 GLM",             "open.bigmodel.cn"),
    "moonshot":  ("Moonshot (月之暗面)",    "api.moonshot.cn"),
    "ollama":    ("Ollama (本地部署)",      "localhost:11434"),
}

# 每个厂商的常用模型列表
_COMMON_MODELS = {
    "deepseek":  ["deepseek-chat", "deepseek-reasoner"],
    "openai":    ["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
    "qwen":      ["qwen-plus", "qwen-max", "qwen-turbo"],
    "glm":       ["glm-4-plus", "glm-4-flash", "glm-4-long"],
    "moonshot":  ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    "ollama":    ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b", "deepseek-r1:8b"],
}

_PROVIDER_KEYS = list(PROVIDER_PRESETS.keys())


def _env_path() -> Path:
    return Path(__file__).resolve().parent / ".env"


def _read_env() -> dict[str, str]:
    """解析当前 .env，返回 key → value"""
    env_file = _env_path()
    if not env_file.exists():
        return {}
    result = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            result[key] = val
    return result


def _write_env(vars: dict[str, str]):
    """写入 .env，按分区组织并保留非 LLM_ / WECHAT_ 的旧行"""
    env_file = _env_path()
    old = _read_env()

    merged = {k: v for k, v in old.items() if not k.startswith(("LLM_", "WECHAT_"))}
    merged.update(vars)

    lines = []
    sections = {
        "一、LLM 连接（自动生成）": ["LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"],
        "二、LLM 行为参数":         ["LLM_TEMPERATURE", "LLM_MAX_TOKENS"],
        "三、上下文管理":           ["LLM_CONTEXT_WINDOW", "LLM_COMPRESSION_RATIO"],
    }

    lines.append("# A_stock 配置文件")
    lines.append("# 由 a-stock setup 生成")
    lines.append("")

    for title, keys in sections.items():
        lines.append(f"# {title}")
        lines.append("")
        for key in keys:
            if key in merged:
                lines.append(f"{key}={merged.pop(key)}")
        lines.append("")

    if merged:
        lines.append("# 其他配置")
        lines.append("")
        for key, val in merged.items():
            lines.append(f"{key}={val}")
        lines.append("")

    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prompt(text: str, default: str = "") -> str:
    """带默认值的输入"""
    hint = f" [{default}]" if default else ""
    val = input(f"  {text}{hint}: ").strip()
    return val if val else default


def _masked(s: str, show: int = 6) -> str:
    """脱敏"""
    if len(s) <= show:
        return "*" * len(s)
    return s[:show] + "…" + s[-4:]


def _test_connection(provider: str, api_key: str, model: str, base_url: str) -> bool:
    """调用 /models 端点测试连通性"""
    print("\n  ⏳ 测试连接…")
    try:
        import urllib.request
        import json as _json

        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = _json.loads(resp.read().decode())
        model_ids = [m.get("id", "") for m in data.get("data", [])]
        if model_ids:
            print(f"  ✅ 连接成功！可用模型: {', '.join(model_ids[:5])}…")
            return True
        else:
            print("  ⚠️  连接成功但未返回模型列表")
            return False
    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "unauthorized" in msg:
            print("  ❌ API Key 无效，请检查后重试")
        elif "403" in msg:
            print("  ❌ 无权限访问，请检查 API Key 权限")
        elif "timeout" in msg or "timed" in msg:
            print("  ⚠️  连接超时，base_url 是否正确？")
        else:
            print(f"  ⚠️  连接失败: {e}")
        return False


def run_setup():
    """交互式配置向导：三要素 → provider / model / api_key → 其余自动补齐"""

    # 读取现有配置
    old = _read_env()
    cur_provider = old.get("LLM_PROVIDER", "")
    cur_api_key = old.get("LLM_API_KEY", "")
    cur_model = old.get("LLM_MODEL", "")

    # ════════════════ 第 1 步：选择厂商 ════════════════

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║     A_stock 配置向导                  ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print("  三要素：厂商 → 模型 → API Key，其余自动补齐。")
    print()
    print("  请选择模型提供商:")

    for i, key in enumerate(_PROVIDER_KEYS, 1):
        name, host = _PROVIDER_DISPLAY.get(key, (key, ""))
        mark = " ← 当前" if cur_provider == key else ""
        print(f"    [{i}] {name}  ({host}){mark}")
    print(f"    [{len(_PROVIDER_KEYS) + 1}] 自定义")

    print()

    default_idx = _PROVIDER_KEYS.index(cur_provider) + 1 if cur_provider in _PROVIDER_KEYS else 1
    choice = _prompt("请输入编号", str(default_idx))

    if not choice.isdigit():
        choice = str(default_idx)

    idx = int(choice)
    if 1 <= idx <= len(_PROVIDER_KEYS):
        provider = _PROVIDER_KEYS[idx - 1]
    else:
        provider = "custom"

    # ════════════════ 第 2 步：选择模型 ════════════════

    preset = PROVIDER_PRESETS.get(provider, {})
    default_model = preset.get("default_model", "deepseek-chat")

    print()
    print("  ─── 选择模型 ───")
    if provider == "custom":
        print("  自定义厂商请手动输入模型名称")
    else:
        name, _host = _PROVIDER_DISPLAY.get(provider, (provider, ""))
        print(f"  {name} 常用模型:")
        common = _COMMON_MODELS.get(provider, [default_model])
        for i, m in enumerate(common, 1):
            mark = " ← 推荐" if m == default_model else ""
            print(f"    [{i}] {m}{mark}")
        if not cur_model or cur_model == default_model:
            print(f"    [m] 手动输入其他模型名")

    model_default = cur_model or default_model
    print()

    if provider != "custom":
        model_choice = _prompt("选择模型 (编号/m/回车=推荐)", str(default_model))
        if model_choice.isdigit():
            mi = int(model_choice) - 1
            if 0 <= mi < len(_COMMON_MODELS.get(provider, [default_model])):
                model = _COMMON_MODELS[provider][mi]
            else:
                model = default_model
        elif model_choice.lower() == "m":
            model = _prompt("请输入模型名称", default_model)
        elif model_choice == str(default_model):
            model = model_default
        else:
            model = model_choice if model_choice else model_default
    else:
        model = _prompt("模型名称", model_default)

    # ════════════════ 第 3 步：API Key ════════════════

    print()
    if provider == "ollama":
        print("  Ollama 本地部署无需 API Key")
        api_key = ""
    else:
        name, host = _PROVIDER_DISPLAY.get(provider, (provider, ""))
        print(f"  {name} ({host})")
        default_display = _masked(cur_api_key) if cur_api_key else ""
        api_key = _prompt("API Key", default_display) if default_display else _prompt("API Key")
        if api_key == default_display:
            api_key = cur_api_key  # 用户没改

    # ════════════════ 第 4 步：确认 — 自动补齐参数 ════

    default_base_url = preset.get("base_url", "https://api.deepseek.com/v1")
    default_context = preset.get("context_window", 65536)

    print()
    print("  ─── 汇总确认 ───")
    print(f"    LLM_PROVIDER          = {provider}")
    if api_key:
        print(f"    LLM_API_KEY           = {_masked(api_key)}")
    print(f"    LLM_MODEL             = {model}")
    print(f"    LLM_BASE_URL          = {default_base_url}    (自动补齐)")
    print(f"    LLM_TEMPERATURE       = 0.7                (默认)")
    print(f"    LLM_MAX_TOKENS        = 4096               (默认)")
    print(f"    LLM_CONTEXT_WINDOW    = {default_context}   (自动补齐)")
    print(f"    LLM_COMPRESSION_RATIO = 0.6                (默认)")

    print()
    confirm = _prompt("确认写入？[Y/n]", "Y")
    if confirm.lower() not in ("y", "yes", ""):
        print("  👋 已取消\n")
        return

    _write_env({
        "LLM_PROVIDER":          provider,
        "LLM_API_KEY":           api_key,
        "LLM_MODEL":             model,
        "LLM_BASE_URL":          default_base_url,
        "LLM_TEMPERATURE":       "0.7",
        "LLM_MAX_TOKENS":        "4096",
        "LLM_CONTEXT_WINDOW":    str(default_context),
        "LLM_COMPRESSION_RATIO": "0.6",
    })

    print()
    print(f"  ✅ 配置已保存到 .env")

    # ════════════════ 可选：测试连接 ════════════════

    print()
    if api_key and provider != "custom":
        test_choice = _prompt("是否测试 API 连接？[y/N]", "N")
        if test_choice.lower() in ("y", "yes"):
            _test_connection(provider, api_key, default_model, default_base_url)

    print()
    print(f"  使用 'a-stock chat' 开始对话\n")


if __name__ == "__main__":
    run_setup()
