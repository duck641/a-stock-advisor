"""
买卖信号判断工具 - 综合基本面、技术面、估值指标给出买入/卖出/持有建议

通过调用其他工具的结果作为输入，由 LLM 或规则引擎做出判断。
"""

from langchain_core.tools import tool
from datetime import datetime


# ════════════════════════════════════════════════
# 辅助：评分等级
# ════════════════════════════════════════════════

def _score_to_rating(score: float) -> str:
    """分数转评级"""
    if score >= 80:
        return "强烈推荐"
    elif score >= 65:
        return "推荐买入"
    elif score >= 50:
        return "谨慎持有"
    elif score >= 35:
        return "建议观望"
    else:
        return "建议卖出"


def _check_fundamental(fundamental: dict) -> tuple[float, list[str]]:
    """
    评估基本面，返回 (得分, 理由列表)

    输入来自 get_fundamental_info 的返回
    """
    score = 50  # 基础分
    reasons = []

    profitability = fundamental.get("盈利能力", {})
    growth = fundamental.get("盈利成长性", {})
    finance = fundamental.get("财务状况", {})

    # 盈利能力评分
    roe = profitability.get("净资产收益率", "0%")
    try:
        roe_val = float(roe.replace("%", ""))
        if roe_val > 15:
            score += 15
            reasons.append(f"ROE({roe_val:.1f}%)优秀，盈利能力强劲")
        elif roe_val > 8:
            score += 8
            reasons.append(f"ROE({roe_val:.1f}%)良好")
        elif roe_val > 0:
            score += 2
            reasons.append(f"ROE({roe_val:.1f}%)偏低")
        else:
            score -= 15
            reasons.append(f"ROE({roe_val:.1f}%)为负，盈利能力堪忧")
    except (ValueError, TypeError):
        pass

    net_profit_rate = profitability.get("销售净利率", "0%")
    try:
        npr = float(net_profit_rate.replace("%", ""))
        if npr > 15:
            score += 10
            reasons.append(f"净利率({npr:.1f}%)较高")
        elif npr > 5:
            score += 5
        elif npr <= 0:
            score -= 10
            reasons.append("净利率为负或极低")
    except (ValueError, TypeError):
        pass

    # 成长性评分
    revenue_growth = growth.get("主营业务收入增长率", "0%")
    profit_growth = growth.get("净利润增长率", "0%")
    try:
        rg = float(revenue_growth.replace("%", ""))
        if rg > 20:
            score += 10
            reasons.append(f"营收增长({rg:.1f}%)快速增长")
        elif rg > 5:
            score += 5
        elif rg < 0:
            score -= 5
            reasons.append("营收负增长")
    except (ValueError, TypeError):
        pass

    try:
        pg = float(profit_growth.replace("%", ""))
        if pg > 20:
            score += 10
            reasons.append(f"净利润增长({pg:.1f}%)高速增长")
        elif pg > 5:
            score += 5
        elif pg < 0:
            score -= 10
            reasons.append(f"净利润下滑({pg:.1f}%)")
    except (ValueError, TypeError):
        pass

    # 财务健康
    debt_ratio = finance.get("资产负债率", "0%")
    try:
        dr = float(debt_ratio.replace("%", ""))
        if dr < 30:
            score += 5
            reasons.append(f"资产负债率({dr:.1f}%)较低，财务稳健")
        elif dr > 70:
            score -= 10
            reasons.append(f"资产负债率({dr:.1f}%)过高，财务风险大")
        else:
            score += 2
    except (ValueError, TypeError):
        pass

    current_ratio = finance.get("流动比率", 0)
    try:
        cr = float(current_ratio)
        if cr > 2:
            score += 5
            reasons.append("流动性充裕")
        elif cr < 1:
            score -= 5
            reasons.append(f"流动比率({cr:.2f})偏低，短期偿债压力大")
    except (ValueError, TypeError):
        pass

    return score, reasons


def _check_technical(technical: dict) -> tuple[float, list[str]]:
    """
    评估技术面，返回 (得分, 理由列表)

    输入来自 get_technical_info 的返回
    """
    score = 50
    reasons = []

    signals = technical.get("买卖信号", {})
    trend_data = technical.get("价格趋势", {})
    vol_data = technical.get("量价关系", {})

    # RSI 评分
    rsi = signals.get("RSI", 50)
    try:
        rsi = float(rsi)
        if rsi < 30:
            score += 20
            reasons.append(f"RSI({rsi:.1f})超卖，存在反弹机会")
        elif rsi < 40:
            score += 10
            reasons.append(f"RSI({rsi:.1f})偏低，接近超卖")
        elif rsi > 80:
            score -= 20
            reasons.append(f"RSI({rsi:.1f})严重超买，回调风险大")
        elif rsi > 70:
            score -= 10
            reasons.append(f"RSI({rsi:.1f})超买")
        else:
            score += 5
            reasons.append(f"RSI({rsi:.1f})处于正常区间")
    except (ValueError, TypeError):
        pass

    # MACD 评分
    macd_signal = signals.get("MACD信号", "")
    if "金叉" in str(macd_signal):
        score += 15
        reasons.append("MACD金叉，买入信号")
    elif "死叉" in str(macd_signal):
        score -= 15
        reasons.append("MACD死叉，卖出信号")
    elif "多头" in str(macd_signal):
        score += 10
        reasons.append("MACD处于多头市场")
    elif "空头" in str(macd_signal):
        score -= 10
        reasons.append("MACD处于空头市场")

    # 趋势评分
    trend = str(trend_data.get("趋势方向", ""))
    if "上涨" in trend:
        score += 10
        reasons.append("短期趋势向上")
    elif "下跌" in trend:
        score -= 10
        reasons.append("短期趋势向下")

    # 量价评分
    vol_signal = str(vol_data.get("量价信号", ""))
    if "上涨有效" in vol_signal:
        score += 10
        reasons.append("价涨量增，上涨有效")
    elif "上涨乏力" in vol_signal:
        score -= 5
        reasons.append("价涨量缩，上涨乏力")
    elif "下跌有效" in vol_signal:
        score -= 10
        reasons.append("价跌量增，下跌有效")
    elif "下跌趋缓" in vol_signal:
        score += 5
        reasons.append("价跌量缩，下跌动能减弱")

    # 均线形态
    ma = trend_data.get("MA5", 0)
    ma20 = trend_data.get("MA20", 0)
    ma60 = trend_data.get("MA60", 0)
    try:
        ma_f, ma20_f, ma60_f = float(ma), float(ma20), float(ma60)
        if ma60 and ma_f > ma20_f > ma60_f:
            score += 10
            reasons.append("均线多头排列")
        elif ma60 and ma_f < ma20_f < ma60_f:
            score -= 10
            reasons.append("均线空头排列")
    except (ValueError, TypeError):
        pass

    # 布林带位置
    boll_signal = str(signals.get("布林信号", ""))
    if "超卖" in boll_signal:
        score += 8
        reasons.append("触及布林下轨，超卖")
    elif "超买" in boll_signal:
        score -= 8
        reasons.append("触及布林上轨，超买")

    return score, reasons


def _check_valuation(fundamental: dict) -> tuple[float, list[str]]:
    """
    评估估值，返回 (得分, 理由列表)

    输入来自 get_fundamental_info 的返回
    """
    score = 50
    reasons = []

    valuation = fundamental.get("估值指标", {})

    # 市盈率判断
    pe = valuation.get("市盈率", None)
    if pe:
        try:
            pe_val = float(pe)
            if pe_val < 15:
                score += 15
                reasons.append(f"市盈率({pe_val:.1f})较低，估值合理")
            elif pe_val < 25:
                score += 8
                reasons.append(f"市盈率({pe_val:.1f})适中")
            elif pe_val < 40:
                score += 2
                reasons.append(f"市盈率({pe_val:.1f})偏高")
            else:
                score -= 10
                reasons.append(f"市盈率({pe_val:.1f})过高，估值泡沫风险")
        except (ValueError, TypeError):
            pass

    # 市净率判断
    pb = valuation.get("市净率", None)
    if pb:
        try:
            pb_val = float(pb)
            if pb_val < 1.5:
                score += 10
                reasons.append(f"市净率({pb_val:.2f})较低")
            elif pb_val < 3:
                score += 5
            elif pb_val > 5:
                score -= 5
                reasons.append(f"市净率({pb_val:.2f})偏高")
        except (ValueError, TypeError):
            pass

    # 市销率
    ps = valuation.get("市销率", None)
    if ps:
        try:
            ps_val = float(ps)
            if ps_val < 2:
                score += 5
        except (ValueError, TypeError):
            pass

    return score, reasons


# ════════════════════════════════════════════════
# 主工具函数
# ════════════════════════════════════════════════

@tool
def generate_trade_signal(
    stock_num: str = "",
    stock_name: str = "",
    fundamental: dict = None,
    technical: dict = None,
) -> dict:
    """
    综合基本面、技术面、估值指标生成买卖信号。

    Dependencies: [get_fundamental_info, get_technical_info]

    建议配合以下工具使用：
      1. get_fundamental_info → 传入 fundamental
      2. get_technical_info   → 传入 technical

    参数:
        stock_num: 6位股票代码
        stock_name: 股票名称（可选）
        fundamental: get_fundamental_info 的返回结果（字典）
        technical: get_technical_info 的返回结果（字典）

    返回:
        字典包含综合评分、操作建议、详细理由
    """
    result = {
        "股票代码": stock_num or "未知",
        "股票名称": stock_name or "",
        "分析时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    all_reasons = []
    total_score = 50  # 基础分
    weights = {"fundamental": 0.35, "technical": 0.40, "valuation": 0.25}

    # 1. 基本面评分
    if fundamental:
        f_score, f_reasons = _check_fundamental(fundamental)
        total_score += (f_score - 50) * weights["fundamental"]
        all_reasons.extend(f_reasons)
        result["基本面评分"] = round(f_score, 1)
        result["基本面评价"] = _score_to_rating(f_score)
    else:
        all_reasons.append("未提供基本面数据，评分中性")
        result["基本面评分"] = 50

    # 2. 技术面评分
    if technical:
        t_score, t_reasons = _check_technical(technical)
        total_score += (t_score - 50) * weights["technical"]
        all_reasons.extend(t_reasons)
        result["技术面评分"] = round(t_score, 1)
        result["技术面评价"] = _score_to_rating(t_score)
    else:
        all_reasons.append("未提供技术面数据，评分中性")
        result["技术面评分"] = 50

    # 3. 估值评分
    if fundamental:
        v_score, v_reasons = _check_valuation(fundamental)
        total_score += (v_score - 50) * weights["valuation"]
        all_reasons.extend(v_reasons)
        result["估值评分"] = round(v_score, 1)
        result["估值评价"] = _score_to_rating(v_score)
    else:
        result["估值评分"] = 50

    # 4. 综合判断
    total_score = min(max(total_score, 0), 100)  # 限制在 0-100
    result["综合评分"] = round(total_score, 1)
    result["操作建议"] = _score_to_rating(total_score)

    if total_score >= 65:
        result["建议仓位"] = "可考虑建仓或加仓"
    elif total_score >= 50:
        result["建议仓位"] = "持仓不动或小幅调整"
    elif total_score >= 35:
        result["建议仓位"] = "减仓观望"
    else:
        result["建议仓位"] = "清仓或空仓回避"

    result["主要理由"] = all_reasons[:8]  # 最多8条

    # 5. 一句话总结
    summary_parts = []
    if fundamental:
        summary_parts.append(f"基本面{result['基本面评价']}")
    if technical:
        summary_parts.append(f"技术面{result['技术面评价']}")
    if fundamental:
        summary_parts.append(f"估值{result['估值评价']}")

    summary = f"综合{result['操作建议']}（{result['综合评分']}分）"
    if summary_parts:
        summary += " | " + " | ".join(summary_parts)
    result["综合摘要"] = summary

    return result
