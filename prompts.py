"""
提示词管理模块

组织结构：
  1. system/   — 系统角色设定（你是谁）
  2. rules/    — 行为规范、强制约束（你必须怎么做）
  3. skills/   — 专业技能、分析框架（你能做什么）
  4. nodes/    — 各节点的专用提示词
"""

from pathlib import Path
from typing import Optional


# ══════════════════════════════════════════
# 技能索引：扫描 skills/ 目录，提取 SKILL.md 摘要
# ══════════════════════════════════════════

SKILLS_DIR = Path(__file__).resolve().parent / "skills"
TRADING_RULES_PATH = Path(__file__).resolve().parent / "trading_rules.md"
STOCK_SELECTION_PATH = Path(__file__).resolve().parent / "stock_selection.md"

_skills_index_cache: Optional[str] = None
_trading_rules_cache: Optional[str] = None
_stock_selection_cache: Optional[str] = None


def _parse_frontmatter(content: str) -> dict:
    """简易解析 YAML frontmatter（只提取 name + description，不依赖 pyyaml）"""
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    meta = {}
    for line in content[3:end].strip().split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key in ("name", "description"):
                meta[key] = val
    return meta


def scan_skills() -> list[dict]:
    """扫描 skills/ 目录，返回每个技能的基本信息"""
    if not SKILLS_DIR.exists():
        return []

    result = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        sk_path = d / "SKILL.md"
        if not sk_path.exists():
            continue

        content = sk_path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(content)
        name = meta.get("name", d.name)
        desc = meta.get("description", "")

        result.append({
            "name": name,
            "description": desc,
        })
    return result


def build_skills_index() -> str:
    """构建技能索引文本（带缓存），供 system prompt 使用"""
    global _skills_index_cache
    if _skills_index_cache is not None:
        return _skills_index_cache

    skills = scan_skills()
    if not skills:
        _skills_index_cache = ""
        return ""

    lines = ["## 可用技能模块", ""]
    for s in skills:
        lines.append(f"  {s['name']}")
        lines.append(f"    说明：{s['description']}")
        lines.append("")
    lines.append("加载方法：使用 `load_skill(\"<技能名称>\")` 加载完整技能详情，")
    lines.append("使用 `list_skills()` 查看所有可用技能列表。")
    lines.append("加载后按技能中的操作步骤执行分析流程。")

    _skills_index_cache = "\n".join(lines)
    return _skills_index_cache


def load_trading_rules() -> str:
    """加载用户自定义交易纪律（带缓存）。

    从项目根目录的 trading_rules.md 读取。
    文件不存在时返回空字符串（不报错，优雅降级）。
    用户可自行编辑该文件来定制交易纪律。
    """
    global _trading_rules_cache
    if _trading_rules_cache is not None:
        return _trading_rules_cache

    if not TRADING_RULES_PATH.exists():
        _trading_rules_cache = ""
        return ""

    content = TRADING_RULES_PATH.read_text(encoding="utf-8").strip()
    if not content:
        _trading_rules_cache = ""
        return ""

    _trading_rules_cache = content
    return _trading_rules_cache


def load_stock_selection() -> str:
    """加载选股策略（带缓存）。

    从项目根目录的 stock_selection.md 读取。
    文件内含短线、长线两部分，Agent 根据用户意图选择匹配的策略。
    文件不存在时返回空字符串。
    """
    global _stock_selection_cache
    if _stock_selection_cache is not None:
        return _stock_selection_cache

    if not STOCK_SELECTION_PATH.exists():
        _stock_selection_cache = ""
        return ""

    content = STOCK_SELECTION_PATH.read_text(encoding="utf-8").strip()
    _stock_selection_cache = content if content else ""
    return _stock_selection_cache

# ══════════════════════════════════════════
# 第一部分：系统角色设定（你是谁）
# ══════════════════════════════════════════

ROLE_ANALYST = """你是一位专业的 A 股量化分析师，拥有十年以上证券市场实战经验。
你擅长通过技术指标分析判断股票走势，并给出可操作的投资建议。"""

ROLE_ASSISTANT = """你是一位专业的投资研究助理，擅长收集和整理金融数据，
协助分析师完成股票研究工作。"""


# ══════════════════════════════════════════
# 第二部分：行为规范（强制约束）
# ══════════════════════════════════════════

RULES_BASE = """## 行为规范

1. 严格依据提供的技术指标数据做判断，不凭空猜测
2. 所有建议必须附带风险提示
3. 回答简洁直接，不使用客套话和废话
4. 不确定时明确说"不确定"，不模糊表达"""

RULES_RISK = """## 风险控制

1. 每笔交易必须给出止损位
2. 不建议单只股票仓位超过总资金的 30%
3. 趋势不明朗时建议观望，不强行交易
4. 连续亏损两次后应暂停交易，重新评估策略"""

RULES_STOCK_RESOLVE = """## 股票代码解析规则

所有分析工具都需要 6 位数字股票代码才能调用。请按以下规则处理输入：

1. 如果用户提供了股票名称但没有代码 → 优先调用 `complete_stock_info` 补全代码
2. 如果用户提供了 6 位代码但没有名称 → 直接使用代码，`complete_stock_info` 不是必须的
3. 如果用户输入的是简称或模糊名称（如"茅台"而不是"贵州茅台"） → 调用 `complete_stock_info` 尝试解析
4. 如果用户输入不涉及任何具体股票（只问大盘、概念知识等） → 直接回答，无需补全
5. 如果信息不足以确定是哪只股票 → 主动向用户询问具体代码或名称"""


# ══════════════════════════════════════════
# 第三部分：技能提示（专业分析能力）
# ══════════════════════════════════════════

SKILL_TREND_ANALYSIS = """## 趋势分析技能

- 上升趋势：高点不断抬高，低点不断抬高 → 优先考虑买入
- 下降趋势：高点不断降低，低点不断降低 → 优先考虑卖出或观望
- 横盘震荡：价格在一定区间内波动 → 高抛低吸或观望
- 突破确认：放量突破阻力位视为有效突破，缩量突破视为假突破"""

SKILL_INDICATOR_READING = """## 技术指标解读

均线：
- 多头排列（短期均线 > 中期 > 长期）：上涨趋势
- 空头排列（短期 < 中期 < 长期）：下跌趋势
- 金叉（短期上穿长期）：买入信号
- 死叉（短期下穿长期）：卖出信号

RSI：
- RSI > 70：超买，可能回调
- RSI < 30：超卖，可能反弹
- RSI 在 30-70 之间：正常区间

MACD：
- DIF 上穿 DEA（金叉）：买入信号
- DIF 下穿 DEA（死叉）：卖出信号
- DIF > 0 且 DEA > 0：多头市场
- DIF < 0 且 DEA < 0：空头市场

布林带：
- 价格触及上轨：超买
- 价格触及下轨：超卖
- 带宽收窄：可能变盘"""

SKILL_VOLUME_ANALYSIS = """## 成交量分析

- 价涨量增：上涨有效，趋势可持续
- 价涨量缩：上涨乏力，可能回调
- 价跌量增：下跌有效，可能继续跌
- 价跌量缩：下跌动能减弱，可能止跌
- 地量：可能见底
- 天量：可能见顶"""

SKILL_PORTFOLIO_MANAGEMENT = """## 仓位管理

- 单只股票不超过总仓位 30%
- 首次建仓不超过计划仓位的 50%（留有余地加仓）
- 止损位设在支撑位下方 3%-5%
- 止盈位分批设置：第一目标 10%，第二目标 20%"""


# ══════════════════════════════════════════
# 第四部分：节点专用提示词
# ══════════════════════════════════════════

REACT_WORKFLOW = """## 工作方式

你可以使用以下工具来获取所需信息。每次思考后，决定是调用工具还是直接回答。

可用的工具包括：
- 个股信息类：查询实时行情、基本资料
- 基本面类：获取盈利能力、财务状况、估值指标
- 技术面类：获取价格趋势、买卖信号、量价关系
- 历史行情类：获取日线历史、历史分时数据
- 市场环境类：了解行业板块、大盘环境、市场情绪
- 买卖信号类：综合基本面+技术面+估值生成信号
- 技能类：使用 list_skills() 查看可用技能，使用 load_skill(<名称>) 加载技能详情并按步骤执行

## 决策流程

1. 分析用户问题，判断需要哪些数据
2. 调用合适的工具获取数据（一次可以调多个独立工具）
3. 查看工具返回结果
4. 如果数据不足，继续调用工具
5. 数据充足后，给出完整分析报告

## 输出要求

- 每个分析必须包含：操作建议、理由、风险提示
- 不确定时明确说"不确定"
- 不使用客套话"""


# ══════════════════════════════════════════
# 便捷拼接函数
# ══════════════════════════════════════════

def build_system_prompt(
    role: str = "analyst",
    include_rules: list[str] | None = None,
    include_skills: list[str] | None = None,
) -> str:
    """
    拼接完整的系统提示词。

    参数:
        role: "analyst" 或 "assistant"
        include_rules: 要包含的规则模块名列表
                       options: ["base", "risk", "format", "stock_resolve"]
                       默认全部包含
        include_skills: 要包含的技能模块名列表
                        options: ["trend", "indicator", "volume", "portfolio"]
                        默认全部包含

    返回:
        拼接后的完整 system prompt
    """
    parts = []

    # 1. 角色设定
    if role == "analyst":
        parts.append(ROLE_ANALYST)
    elif role == "assistant":
        parts.append(ROLE_ASSISTANT)

    parts.append("")

    # 2. 行为规范
    rule_map = {
        "base": RULES_BASE,
        "risk": RULES_RISK,
        "stock_resolve": RULES_STOCK_RESOLVE,
    }
    selected_rules = include_rules or list(rule_map.keys())
    for name in selected_rules:
        if name in rule_map:
            parts.append(rule_map[name])
            parts.append("")

    # 3. 选股策略（从 stock_selection.md 加载，含短线/长线）
    strategy = load_stock_selection()
    if strategy:
        parts.append(strategy)
        parts.append("")

    # 4. 用户交易纪律（从 trading_rules.md 加载）
    trading_rules = load_trading_rules()
    if trading_rules:
        parts.append(trading_rules)
        parts.append("")

    # 5. 技能提示
    skill_map = {
        "trend": SKILL_TREND_ANALYSIS,
        "indicator": SKILL_INDICATOR_READING,
        "volume": SKILL_VOLUME_ANALYSIS,
        "portfolio": SKILL_PORTFOLIO_MANAGEMENT,
    }
    selected_skills = include_skills or list(skill_map.keys())
    for name in selected_skills:
        if name in skill_map:
            parts.append(skill_map[name])
            parts.append("")

    return "\n".join(parts).strip()


def build_react_system_prompt(
    role: str = "analyst",
    include_rules: list[str] | None = None,
    include_skills: list[str] | None = None,
    include_skills_index: bool = True,
) -> str:
    """
    拼接 ReAct 主循环使用的完整系统提示词。

    包含：角色设定 + 行为规范 + 专业技能 + 工作方式说明 + 可用技能索引

    参数:
        role: "analyst" 或 "assistant"
        include_rules: 要包含的规则模块，默认全部
        include_skills: 要包含的技能模块，默认全部
        include_skills_index: 是否附加 skills/ 目录下的技能索引
    """
    base = build_system_prompt(role=role, include_rules=include_rules, include_skills=include_skills)
    workflow = REACT_WORKFLOW
    if include_skills_index:
        idx = build_skills_index()
        if idx:
            workflow += "\n\n" + idx
    return base + "\n\n" + workflow
