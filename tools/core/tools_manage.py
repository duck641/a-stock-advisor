"""
工具管理 - 统一管理和查看所有工具

提供工具列表查询、分类查看、快速调用入口。
"""

from typing import Optional


def _get_tools():
    """延迟导入，避免循环引用"""
    from tools import TOOLS
    return TOOLS


def list_tools(category: Optional[str] = None) -> list[dict]:
    """
    列出所有可用工具，可按分类筛选。

    参数:
        category: 分类筛选，可选值：
                  "stock" - 个股信息
                  "history" - 历史行情
                  "fundamental" - 基本面
                  "technical" - 技术面
                  "market" - 市场环境
                  "signal" - 买卖信号
                  不传则返回全部

    返回:
        工具信息列表，每个工具包含 name, description, params, category
    """
    TOOLS = _get_tools()
    category_map = {
        "select_signal_stock": "stock",
        "get_realtime_quote": "stock",
        "get_stock_basic_info": "stock",
        "search_stock_by_name": "stock",
        "get_fundamental_info": "fundamental",
        "get_technical_info": "technical",
        "get_stock_daily_history": "history",
        "get_stock_history_intraday": "history",
        "get_market_sentiment": "market",
        "get_market_environment": "market",
        "get_cls_morning_report": "market",
        "get_project_daily_report": "market",
        "get_sector_rotation_context": "market",
        "get_sector_rankings": "market",
        "get_sector_leaders": "market",
        "get_sector_history": "market",
        "get_investment_calendar": "market",
        "generate_trade_signal": "signal",
    }

    result = []
    for tool in TOOLS:
        tool_cat = category_map.get(tool.name, "other")
        if category and tool_cat != category:
            continue

        params = []
        if tool.args_schema:
            for name, field in tool.args_schema.model_fields.items():
                param_info = {
                    "name": name,
                    "type": str(field.annotation) if field.annotation else "str",
                    "required": field.is_required() if hasattr(field, "is_required") else True,
                    "description": field.description or "",
                }
                if not field.is_required() and field.default is not None:
                    param_info["default"] = field.default
                    param_info["required"] = False
                params.append(param_info)

        result.append({
            "name": tool.name,
            "category": tool_cat,
            "description": tool.description.split("\n")[0] if tool.description else "",
            "params": params,
        })

    if category:
        return result
    return result


def show_tool_help(tool_name: str) -> str:
    """查看指定工具的详细帮助信息。"""
    TOOLS = _get_tools()
    for tool in TOOLS:
        if tool.name == tool_name:
            lines = []
            lines.append(f"╔══ {tool.name} ═══════════════════════════════")
            lines.append(f"║  {tool.description}")
            lines.append("║")
            if tool.args_schema:
                lines.append("║  参数:")
                for name, field in tool.args_schema.model_fields.items():
                    required = "必填" if (field.is_required() if hasattr(field, "is_required") else True) else "可选"
                    default = f" (默认: {field.default})" if not (field.is_required() if hasattr(field, "is_required") else True) and field.default is not None else ""
                    desc = field.description or ""
                    lines.append(f"║    {name}: {desc} [{required}]{default}")
            lines.append("╚" + "═" * 40)
            return "\n".join(lines)
    return f"未找到工具: {tool_name}"


def quick_call(tool_name: str, **kwargs) -> str:
    """快速调用指定工具并返回结果。"""
    TOOLS = _get_tools()
    for tool in TOOLS:
        if tool.name == tool_name:
            try:
                result = tool.invoke(kwargs)
                if hasattr(result, "to_string"):
                    return result.to_string()
                elif hasattr(result, "to_dict"):
                    import json
                    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
                elif isinstance(result, dict):
                    import json
                    return json.dumps(result, ensure_ascii=False, indent=2)
                return str(result)
            except Exception as e:
                return f"调用失败: {e}"
    return f"未找到工具: {tool_name}"


def summary() -> str:
    """工具概况总览：各类工具数量统计。"""
    categories = {}
    for t in list_tools():
        cat = t["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(t["name"])

    lines = []
    lines.append("股票分析工具包总览")
    lines.append("=" * 40)
    for cat, tools in categories.items():
        lines.append(f"  {cat}: {len(tools)} 个工具")
        for t in tools:
            lines.append(f"    • {t}")
    TOOLS = _get_tools()
    lines.append("=" * 40)
    lines.append(f"  共 {len(TOOLS)} 个工具")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "summary":
        print(summary())
    elif len(sys.argv) > 1 and sys.argv[1] == "help":
        if len(sys.argv) > 2:
            print(show_tool_help(sys.argv[2]))
        else:
            print("用法: python tools_manage.py help <工具名>")
    else:
        print(summary())
        print()
        print("查看工具详情: python tools_manage.py help <工具名>")
