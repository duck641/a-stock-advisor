"""
买卖信号判断工具 - 综合基本面、技术面、估值指标给出买入/卖出/持有建议

通过调用其他工具的结果作为输入，由 LLM 或规则引擎做出判断。
"""

from langchain_core.tools import tool
from datetime import datetime
import math


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


def _to_float(value) -> float | None:
    """解析真实数值；None、N/A、空字符串和 NaN 都视为缺失。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def _check_fundamental(fundamental: dict) -> tuple[float, list[str], int]:
    """
    评估基本面，返回 (得分, 理由列表, 有效指标数)

    输入来自 get_fundamental_info 的返回
    """
    score = 50  # 基础分
    reasons = []
    valid_count = 0

    profitability = fundamental.get("盈利能力", {})
    growth = fundamental.get("盈利成长性", {})
    finance = fundamental.get("财务状况", {})

    # 盈利能力评分
    roe_val = _to_float(profitability.get("净资产收益率"))
    if roe_val is not None:
        valid_count += 1
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

    npr = _to_float(profitability.get("销售净利率"))
    if npr is not None:
        valid_count += 1
        if npr > 15:
            score += 10
            reasons.append(f"净利率({npr:.1f}%)较高")
        elif npr > 5:
            score += 5
        elif npr <= 0:
            score -= 10
            reasons.append("净利率为负或极低")

    # 成长性评分
    rg = _to_float(growth.get("主营业务收入增长率"))
    if rg is not None:
        valid_count += 1
        if rg > 20:
            score += 10
            reasons.append(f"营收增长({rg:.1f}%)快速增长")
        elif rg > 5:
            score += 5
        elif rg < 0:
            score -= 5
            reasons.append("营收负增长")

    pg = _to_float(growth.get("净利润增长率"))
    if pg is not None:
        valid_count += 1
        if pg > 20:
            score += 10
            reasons.append(f"净利润增长({pg:.1f}%)高速增长")
        elif pg > 5:
            score += 5
        elif pg < 0:
            score -= 10
            reasons.append(f"净利润下滑({pg:.1f}%)")

    # 财务健康
    dr = _to_float(finance.get("资产负债率"))
    if dr is not None:
        valid_count += 1
        if dr < 30:
            score += 5
            reasons.append(f"资产负债率({dr:.1f}%)较低，财务稳健")
        elif dr > 70:
            score -= 10
            reasons.append(f"资产负债率({dr:.1f}%)过高，财务风险大")
        else:
            score += 2

    cr = _to_float(finance.get("流动比率"))
    if cr is not None:
        valid_count += 1
        if cr > 2:
            score += 5
            reasons.append("流动性充裕")
        elif cr < 1:
            score -= 5
            reasons.append(f"流动比率({cr:.2f})偏低，短期偿债压力大")

    return score, reasons, valid_count


def _check_technical(technical: dict) -> tuple[float, list[str], int]:
    """
    评估技术面，返回 (得分, 理由列表, 有效指标数)

    输入来自 get_technical_info 的返回
    """
    score = 50
    reasons = []
    valid_count = 0

    signals = technical.get("买卖信号", {})
    trend_data = technical.get("价格趋势", {})
    vol_data = technical.get("量价关系", {})

    # RSI 评分
    rsi = _to_float(signals.get("RSI"))
    if rsi is not None:
        valid_count += 1
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

    # MACD 评分
    macd_signal = signals.get("MACD信号", "")
    if macd_signal:
        valid_count += 1
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
    if trend:
        valid_count += 1
    if "上涨" in trend:
        score += 10
        reasons.append("短期趋势向上")
    elif "下跌" in trend:
        score -= 10
        reasons.append("短期趋势向下")

    # 量价评分
    vol_signal = str(vol_data.get("量价信号", ""))
    if vol_signal:
        valid_count += 1
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
    ma_f = _to_float(trend_data.get("MA5"))
    ma20_f = _to_float(trend_data.get("MA20"))
    ma60_f = _to_float(trend_data.get("MA60"))
    if None not in (ma_f, ma20_f, ma60_f):
        valid_count += 1
        if ma_f > ma20_f > ma60_f:
            score += 10
            reasons.append("均线多头排列")
        elif ma_f < ma20_f < ma60_f:
            score -= 10
            reasons.append("均线空头排列")

    # 布林带位置
    boll_signal = str(signals.get("布林信号", ""))
    if boll_signal:
        valid_count += 1
    if "超卖" in boll_signal:
        score += 8
        reasons.append("触及布林下轨，超卖")
    elif "超买" in boll_signal:
        score -= 8
        reasons.append("触及布林上轨，超买")

    return score, reasons, valid_count


def _check_valuation(fundamental: dict) -> tuple[float, list[str], int]:
    """
    评估估值，返回 (得分, 理由列表, 有效指标数)

    输入来自 get_fundamental_info 的返回
    """
    score = 50
    reasons = []
    valid_count = 0

    valuation = fundamental.get("估值指标", {})

    # 市盈率判断
    pe_val = _to_float(valuation.get("市盈率"))
    if pe_val is not None:
        valid_count += 1
        if pe_val > 0:
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
        else:
            reasons.append("市盈率为负或零，不适合按常规市盈率估值")

    # 市净率判断
    pb_val = _to_float(valuation.get("市净率"))
    if pb_val is not None:
        valid_count += 1
        if pb_val > 0:
            if pb_val < 1.5:
                score += 10
                reasons.append(f"市净率({pb_val:.2f})较低")
            elif pb_val < 3:
                score += 5
            elif pb_val > 5:
                score -= 5
                reasons.append(f"市净率({pb_val:.2f})偏高")

    # 市销率
    ps_val = _to_float(valuation.get("市销率"))
    if ps_val is not None:
        valid_count += 1
        if ps_val > 0:
            if ps_val < 2:
                score += 5

    return score, reasons, valid_count


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
        字典包含数据完整度、结论可信度、综合评分、操作建议和详细理由。
        核心数据不足时不生成买卖或仓位建议。
    """
    result = {
        "股票代码": stock_num or "未知",
        "股票名称": stock_name or "",
        "分析时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    fundamental = fundamental if isinstance(fundamental, dict) else {}
    technical = technical if isinstance(technical, dict) else {}
    all_reasons: list[str] = []
    base_weights = {"fundamental": 0.35, "technical": 0.40, "valuation": 0.25}
    expected_counts = {"fundamental": 6, "technical": 6, "valuation": 3}

    # 每个评分函数同时返回有效指标数量。空字典和只有错误信息的字典不会再得到默认 50 分。
    f_score, f_reasons, f_count = _check_fundamental(fundamental)
    t_score, t_reasons, t_count = _check_technical(technical)
    v_score, v_reasons, v_count = _check_valuation(fundamental)
    all_reasons.extend(f_reasons + t_reasons + v_reasons)

    component_scores = {
        "fundamental": (f_score, f_count),
        "technical": (t_score, t_count),
        "valuation": (v_score, v_count),
    }
    component_labels = {
        "fundamental": "基本面",
        "technical": "技术面",
        "valuation": "估值",
    }
    minimum_for_rating = {"fundamental": 3, "technical": 3, "valuation": 2}

    for name, (score, count) in component_scores.items():
        label = component_labels[name]
        result[f"{label}有效指标"] = f"{count}/{expected_counts[name]}"
        result[f"{label}评分"] = round(score, 1) if count else None
        result[f"{label}评价"] = (
            _score_to_rating(score)
            if count >= minimum_for_rating[name]
            else "样本不足"
        )

    valid_count = sum(count for _score, count in component_scores.values())
    expected_total = sum(expected_counts.values())
    completeness = valid_count / expected_total
    missing_data = [
        f"{component_labels[name]}缺少 {expected_counts[name] - count} 项指标"
        for name, (_score, count) in component_scores.items()
        if count < expected_counts[name]
    ]
    result["有效指标数"] = f"{valid_count}/{expected_total}"
    result["数据完整度"] = f"{completeness:.0%}"
    result["缺失数据"] = missing_data

    # 只对实际有数据的维度重新分配权重，缺失维度不会再用中性 50 分稀释结果。
    available_weight = sum(
        base_weights[name]
        for name, (_score, count) in component_scores.items()
        if count > 0
    )
    if available_weight:
        actual_weights = {
            name: base_weights[name] / available_weight
            for name, (_score, count) in component_scores.items()
            if count > 0
        }
        reference_score = sum(
            component_scores[name][0] * weight
            for name, weight in actual_weights.items()
        )
    else:
        actual_weights = {}
        reference_score = None

    result["实际权重"] = {
        component_labels[name]: f"{weight:.0%}"
        for name, weight in actual_weights.items()
    }

    # 交易建议至少需要一半指标有效，并且技术面不能少于三项。
    sufficient = completeness >= 0.5 and t_count >= 3
    confidence = "高" if completeness >= 0.8 and t_count >= 4 else "中" if sufficient else "低"
    result["结论可信度"] = confidence

    if not sufficient:
        result["参考评分"] = round(reference_score, 1) if reference_score is not None else None
        result["综合评分"] = None
        result["操作建议"] = "数据不足，暂不判断"
        result["建议仓位"] = "不建议基于当前数据操作"
        result["主要理由"] = [
            f"仅取得 {valid_count}/{expected_total} 项有效指标，或技术面数据不足",
            *missing_data,
        ][:8]
        result["综合摘要"] = "数据不足，无法形成可靠的买卖结论"
        return result

    # 只有数据达到最低门槛后，才生成综合评级和仓位建议。
    total_score = min(max(reference_score, 0), 100)
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
    summary_parts = [
        f"{component_labels[name]}{result[f'{component_labels[name]}评价']}"
        for name, (_score, count) in component_scores.items()
        if count > 0
    ]

    summary = f"综合{result['操作建议']}（{result['综合评分']}分）"
    if summary_parts:
        summary += " | " + " | ".join(summary_parts)
    result["综合摘要"] = summary

    return result
