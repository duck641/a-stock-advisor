"""
股票信息补全工具 - 代码⇄名称双向查询，参数可选

作用：在 ReAct 循环开始前补全股票信息，确保后续工具能正确调用。
如果用户只提供了股票名称，补全出代码；只提供了代码，补全出名称。

依赖声明：
  - akshare.stock_info_a_code_name  — A股代码/名称全量映射表
  - akshare.stock_intraday_sina    — 新浪日内分时（用于提取股票名称）
"""

from typing import Optional
from datetime import datetime

from tools.stock.select_signal_stock import get_stock_info


# 全局缓存，避免每次调用都拉全量数据
_stock_name_code_cache = None


def _load_stock_name_code():
    """加载股票名称→代码映射表（带缓存，只拉一次）"""
    import akshare as ak
    global _stock_name_code_cache
    if _stock_name_code_cache is not None:
        return _stock_name_code_cache
    df = ak.stock_info_a_code_name()
    _stock_name_code_cache = df
    return df


def resolve_stock(
    stock_num: Optional[str] = None,
    stock_name: Optional[str] = None,
) -> dict:
    """
    解析股票信息，补全缺失的字段。

    支持三种输入方式：
      1. 只传 stock_num（6位代码）→ 返回 代码 + 名称 + 市场
      2. 只传 stock_name（名称）→ 从全量数据中搜索，返回 代码 + 名称 + 市场
      3. 两个都传 → 验证一致性

    参数:
        stock_num: 6位股票代码（可选）
        stock_name: 股票名称（可选）

    返回:
        {"股票代码": "002479", "股票名称": "富春环保", "所属市场": "深圳"}
    """
    import akshare as ak

    result = {}

    # ── 情况1：只有代码 → 查名称 ──
    if stock_num and not stock_name:
        code = stock_num.strip()
        formatted = get_stock_info(code)
        if not formatted:
            raise ValueError(f"无法识别的股票代码: {code}")

        market_map = {"sh": "上海", "sz": "深圳", "bj": "北京"}
        result["所属市场"] = market_map.get(formatted[:2], "未知")

        try:
            today = datetime.now().strftime("%Y%m%d")
            intra = ak.stock_intraday_sina(symbol=formatted, date=today)
            result["股票名称"] = intra["name"].iloc[0] if not intra.empty else code
        except Exception:
            result["股票名称"] = code

        result["股票代码"] = code
        return result

    # ── 情况2：只有名称 → 查代码 ──
    if stock_name and not stock_num:
        name = stock_name.strip()
        df = _load_stock_name_code()

        for _, row in df.iterrows():
            db_name = str(row.get("name", ""))
            db_code = str(row.get("code", ""))
            if name == db_name:
                formatted = get_stock_info(db_code)
                market_map = {"sh": "上海", "sz": "深圳", "bj": "北京"}
                result["所属市场"] = market_map.get(formatted[:2], "未知") if formatted else "未知"
                result["股票代码"] = db_code
                result["股票名称"] = db_name
                return result

        # 精确匹配不到，尝试模糊
        matches = []
        for _, row in df.iterrows():
            db_name = str(row.get("name", ""))
            db_code = str(row.get("code", ""))
            if name in db_name:
                matches.append(f"{db_name}: {db_code}")
        if matches:
            result["模糊匹配"] = matches[:5]
            result["提示"] = f"未精确匹配到'{name}'，以下是包含该关键词的股票"
            return result

        raise ValueError(f"未找到股票: '{stock_name}'，请检查名称是否正确")

    # ── 情况3：代码和名称都传了 → 验证一致性 ──
    if stock_num and stock_name:
        code = stock_num.strip()
        name = stock_name.strip()
        # 通过代码查名称
        formatted = get_stock_info(code)
        if not formatted:
            raise ValueError(f"无法识别的股票代码: {code}")

        market_map = {"sh": "上海", "sz": "深圳", "bj": "北京"}
        result["所属市场"] = market_map.get(formatted[:2], "未知")

        try:
            today = datetime.now().strftime("%Y%m%d")
            intra = ak.stock_intraday_sina(symbol=formatted, date=today)
            actual_name = intra["name"].iloc[0] if not intra.empty else code
        except Exception:
            actual_name = code

        result["股票代码"] = code
        result["股票名称"] = name
        if actual_name != name and actual_name != code:
            result["警告"] = f"名称不匹配：代码{code}对应的实际名称为'{actual_name}'，你提供的是'{name}'"
        return result

    raise ValueError("请至少提供股票代码或股票名称中的一个")


# ── 作为工具的版本（给 LLM 用）──

from langchain_core.tools import tool


@tool
def complete_stock_info(
    stock_num: Optional[str] = "",
    stock_name: Optional[str] = "",
) -> str:
    """
    补全股票信息。通过股票代码查名称，或通过名称查代码。

    如果不知道股票代码，只提供名称即可；
    如果知道代码但不知道名称，只提供代码即可。

    参数:
        stock_num: 6位股票代码（可选），如 "002479"
        stock_name: 股票名称（可选），如 "富春环保"

    返回:
        补全后的股票信息（代码 + 名称）
    """
    sn = stock_num.strip() if stock_num else ""
    nm = stock_name.strip() if stock_name else ""

    if not sn and not nm:
        return "请提供股票代码或股票名称中的至少一个"

    try:
        result = resolve_stock(
            stock_num=sn if sn else None,
            stock_name=nm if nm else None,
        )
        import json
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"查询失败: {e}"
