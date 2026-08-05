"""
股票数据采集工具 - 供 LangGraph ReAct 智能体调用

所有工具使用 @tool 装饰器，LLM 可自动识别并调用。
已验证可用数据源：新浪（分时/实时行情）、腾讯（日线）

依赖声明：
  - akshare.stock_intraday_sina      — 新浪日内分时数据（3秒粒度）
  - akshare.stock_financial_analysis_indicator — 财务分析指标
  - akshare.stock_zh_valuation_baidu — 百度估值指标
  - akshare.stock_zh_a_hist_tx      — 腾讯日线历史数据
  - akshare.stock_zygc_em           — 东方财富主营业务（可能受风控）
  - akshare.stock_lhb_ggtj_sina     — 新浪龙虎榜数据
  - requests（直连新浪 API）          — 实时行情快照
"""

from langchain_core.tools import tool
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta


# ════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════

def get_stock_info(stock_num: str) -> str:
    """根据6位股票代码返回带市场前缀的代码"""
    code_str = str(stock_num).strip()
    if code_str.startswith(("sh", "sz", "bj")):
        return code_str

    if code_str.startswith(("600", "601", "603", "605", "688", "900")):
        return "sh" + code_str
    elif code_str.startswith(("000", "001", "002", "003", "200", "300", "301")):
        return "sz" + code_str
    elif code_str.startswith(("430", "440", "830", "831", "832", "833", "839")):
        return "bj" + code_str
    return None

# ════════════════════════════════════════════════
# 工具1：日内分时数据
# ════════════════════════════════════════════════

@tool
def select_signal_stock(stock_num: str, date: str) -> pd.DataFrame:
    """
    获取单只股票在指定日期的日内分时交易数据（新浪数据源，3秒粒度）

    Dependencies: [complete_stock_info]

    参数:
        stock_num: 6位股票代码，如 "002479"
        date: 8位日期，如 "20260722"

    返回:
        DataFrame包含: symbol, name, ticktime, price, volume, prev_price, kind
        kind字段: U=上涨, D=下跌, E=持平
    """
    if len(stock_num) != 6:
        raise ValueError(f"股票代码必须是6位，当前为{len(stock_num)}位: {stock_num}")
    if len(date) != 8:
        raise ValueError(f"日期必须是8位，当前为{len(date)}位: {date}")

    formatted_stock = get_stock_info(stock_num)
    if not formatted_stock:
        raise ValueError(f"无法识别的股票代码: {stock_num}")

    intraday_df = ak.stock_intraday_sina(symbol=formatted_stock, date=date)
    return intraday_df



# ════════════════════════════════════════════════
# 工具3：实时行情快照
# ════════════════════════════════════════════════

@tool
def get_realtime_quote(stock_num: str) -> dict:
    """
    获取单只股票的实时行情快照（新浪数据源）

    Dependencies: [complete_stock_info]

    包含：名称、开盘价、昨收、当前价、最高、最低、成交量、成交额

    参数:
        stock_num: 6位股票代码，如 "002479"

    返回:
        字典包含实时行情字段
    """
    formatted_stock = get_stock_info(stock_num)
    if not formatted_stock:
        raise ValueError(f"无法识别的股票代码: {stock_num}")

    import requests

    url = f"https://hq.sinajs.cn/list={formatted_stock}"
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.encoding = "gbk"

    text = resp.text
    if "=" not in text:
        return {"error": "未获取到数据"}

    data = text.split('"')[1].split(",")
    fields = [
        ("name", "股票名称"),
        ("open", "开盘价"),
        ("yesterday_close", "昨收"),
        ("current_price", "当前价"),
        ("high", "最高"),
        ("low", "最低"),
        ("bid_price", "买价"),
        ("ask_price", "卖价"),
        ("volume", "成交量(股)"),
        ("amount", "成交额(元)"),
    ]

    result = {}
    for i, (key, label) in enumerate(fields):
        if i < len(data):
            result[label] = data[i].strip()
    return result


# ════════════════════════════════════════════════
# 工具6：公司基本面信息
# ════════════════════════════════════════════════

@tool
def get_fundamental_info(stock_num: str) -> dict:
    """
    获取股票的公司基本面信息，包含盈利能力、财务状况、估值指标。

    Dependencies: [complete_stock_info]

    参数:
        stock_num: 6位股票代码，如 "002479"

    返回:
        字典包含盈利能力、财务状况、估值指标三类数据
    """
    formatted_stock = get_stock_info(stock_num)
    if not formatted_stock:
        raise ValueError(f"无法识别的股票代码: {stock_num}")

    result = {"股票代码": stock_num}

    try:
        # 1. 财务分析指标（盈利能力、成长性）
        df_fin = ak.stock_financial_analysis_indicator(symbol=stock_num, start_year="2025")
        if not df_fin.empty:
            latest = df_fin.iloc[-1]
            result["盈利能力"] = {
                "每股收益": float(latest.get("摊薄每股收益(元)", "N/A")) if isinstance(latest.get("摊薄每股收益(元)"), (int, float)) else str(latest.get("摊薄每股收益(元)", "N/A")),
                "净资产收益率": f"{float(latest.get('净资产收益率(%)', 0)):.2f}%",
                "销售净利率": f"{float(latest.get('销售净利率(%)', 0)):.2f}%",
                "主营业务利润率": f"{float(latest.get('主营业务利润率(%)', 0)):.2f}%",
            }
            result["盈利成长性"] = {
                "主营业务收入增长率": f"{float(latest.get('主营业务收入增长率(%)', 0)):.2f}%",
                "净利润增长率": f"{float(latest.get('净利润增长率(%)', 0)):.2f}%",
            }
            result["财务状况"] = {
                "每股净资产": float(latest.get("每股净资产_调整前(元)", 0)),
                "每股经营性现金流": float(latest.get("每股经营性现金流(元)", 0)),
                "资产负债率": f"{float(latest.get('资产负债率(%)', 0)):.2f}%",
                "流动比率": float(latest.get("流动比率", 0)),
                "速动比率": float(latest.get("速动比率", 0)),
            }
    except Exception as e:
        result["财务分析_错误"] = str(e)

    try:
        # 2. 估值指标（百度股市通）
        df_val = ak.stock_zh_valuation_baidu(symbol=stock_num, indicator="估值指标", period="近一年")
        if not df_val.empty:
            latest_val = df_val.iloc[-1]
            result["估值指标"] = {}
            for col in df_val.columns:
                if col not in ["日期", "股票代码"]:
                    try:
                        result["估值指标"][col] = float(latest_val[col]) if isinstance(latest_val[col], (int, float)) else str(latest_val[col])
                    except (ValueError, TypeError):
                        result["估值指标"][col] = str(latest_val[col])
    except Exception as e:
        try:
            # 备用：获取主营业务构成
            df_zy = ak.stock_zygc_em(symbol=formatted_stock.upper())
            if not df_zy.empty:
                latest_zy = df_zy[df_zy["报告日期"] == df_zy["报告日期"].max()]
                result["主营构成"] = latest_zy[["主营构成", "主营收入", "收入比例", "毛利率"]].to_dict("records")[:5]
        except Exception:
            pass

    return result


# ════════════════════════════════════════════════
# 工具7：技术面信息
# ════════════════════════════════════════════════

@tool
def get_technical_info(stock_num: str) -> dict:
    """
    获取股票的技术面分析信息，包含价格趋势、买卖信号、量价关系。

    Dependencies: [complete_stock_info]

    参数:
        stock_num: 6位股票代码，如 "002479"

    返回:
        字典包含价格趋势、买卖信号、量价关系等指标
    """
    formatted_stock = get_stock_info(stock_num)
    if not formatted_stock:
        raise ValueError(f"无法识别的股票代码: {stock_num}")

    # 先获取足够的历史数据
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")

    df = ak.stock_zh_a_hist_tx(
        symbol=formatted_stock.upper(),
        start_date=start_date,
        end_date=end_date,
    )

    if df.empty:
        return {"错误": "未获取到历史数据"}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["amount"]

    result = {"股票代码": stock_num}

    # 1. 价格趋势
    # 均线
    ma5 = round(close.rolling(5).mean().iloc[-1], 2)
    ma10 = round(close.rolling(10).mean().iloc[-1], 2)
    ma20 = round(close.rolling(20).mean().iloc[-1], 2)
    ma60 = round(close.rolling(60).mean().iloc[-1], 2) if len(df) >= 60 else None

    # 趋势方向（用最近5天线性回归斜率）
    import numpy as np
    x = np.arange(min(5, len(close)))
    slope = np.polyfit(x, close.tail(min(5, len(close))).values, 1)[0]
    trend = "上涨" if slope > 0.1 else "下跌" if slope < -0.1 else "震荡"

    # 支撑阻力
    recent_20 = df.tail(20)
    support = round(recent_20["low"].min(), 2)
    resistance = round(recent_20["high"].max(), 2)

    result["价格趋势"] = {
        "MA5": ma5,
        "MA10": ma10,
        "MA20": ma20,
        "MA60": ma60,
        "最新价": close.iloc[-1],
        "趋势方向": trend,
        "近期支撑": support,
        "近期阻力": resistance,
    }

    # 2. 买卖信号
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    rsi = round((100 - (100 / (1 + rs))).iloc[-1], 2) if not rs.empty else 50.0

    rsi_signal = "超买(考虑卖出)" if rsi > 70 else "超卖(考虑买入)" if rsi < 30 else "中性"

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = round((ema12 - ema26).iloc[-1], 4)
    dea = round((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1], 4)
    macd_val = round(2 * (dif - dea), 4)

    macd_signal = "金叉(买入信号)" if dif > dea and dif < 0 else "死叉(卖出信号)" if dif < dea and dif > 0 else "多头" if dif > 0 and dea > 0 else "空头" if dif < 0 and dea < 0 else "中性"

    # 布林带
    boll_mid = close.rolling(20).mean().iloc[-1]
    boll_std = close.rolling(20).std().iloc[-1]
    boll_up = round(boll_mid + 2 * boll_std, 2)
    boll_dn = round(boll_mid - 2 * boll_std, 2)
    latest_price = close.iloc[-1]

    if latest_price >= boll_up:
        boll_signal = "触及上轨(超买)"
    elif latest_price <= boll_dn:
        boll_signal = "触及下轨(超卖)"
    else:
        boll_signal = "布林带内(正常)"

    result["买卖信号"] = {
        "RSI": rsi,
        "RSI信号": rsi_signal,
        "MACD_DIF": dif,
        "MACD_DEA": dea,
        "MACD柱": macd_val,
        "MACD信号": macd_signal,
        "布林上轨": boll_up,
        "布林中轨": round(boll_mid, 2),
        "布林下轨": boll_dn,
        "布林信号": boll_signal,
    }

    # 3. 量价关系
    vol_ma5 = volume.tail(5).mean()
    vol_ma20 = volume.tail(20).mean()
    latest_vol = volume.iloc[-1]

    # 量价配合
    price_change = close.iloc[-1] - close.iloc[-2] if len(close) > 1 else 0
    if price_change > 0 and latest_vol > vol_ma5:
        vol_price = "价涨量增(上涨有效)"
    elif price_change > 0 and latest_vol < vol_ma5:
        vol_price = "价涨量缩(上涨乏力)"
    elif price_change < 0 and latest_vol > vol_ma5:
        vol_price = "价跌量增(下跌有效)"
    elif price_change < 0 and latest_vol < vol_ma5:
        vol_price = "价跌量缩(下跌趋缓)"
    else:
        vol_price = "量价正常"

    result["量价关系"] = {
        "当日成交额(万元)": round(latest_vol, 0),
        "5日均量": round(vol_ma5, 0),
        "20日均量": round(vol_ma20, 0),
        "量价信号": vol_price,
        "换手率估算": f"{round(latest_vol / vol_ma20 * 100, 2) if vol_ma20 else 0:.2f}%",
    }

    return result


# ════════════════════════════════════════════════
# 工具8：龙虎榜数据
# ════════════════════════════════════════════════

@tool
def get_dragon_tiger_list(days: str = "10") -> str:
    """
    获取A股龙虎榜营业部排名数据（数据源：新浪）。

    龙虎榜是跟踪主力资金动向的重要指标，净买入额高的股票通常代表机构/游资看好。
    当用户要求推荐股票时，优先从龙虎榜中挑选上榜次数多的股票。

    参数:
        days: 统计周期，"5"=近5日，"10"=近10日，"30"=近30日，默认"5"

    返回:
        龙虎榜营业部排名（代码 + 名称 + 上榜次数 + 累积买卖额 + 净额）
    """
    import akshare as ak

    if days not in ("5", "10", "30"):
        return "days 参数仅支持 '5'（近5日）、'10'（近10日）、'30'（近30日）"

    df = ak.stock_lhb_ggtj_sina(symbol=days)
    if df.empty:
        return "暂无龙虎榜数据"

    # 按净买入额降序排列
    df_sorted = df.sort_values("净额", ascending=False)

    lines = [f"📊 A股龙虎榜营业部排名（近{days}日）  |  共 {len(df_sorted)} 只股票上榜", ""]
    lines.append(f"{'代码':<8} {'名称':<10} {'上榜':>4} {'累积买入(万)':>14} {'累积卖出(万)':>14} {'净额(万)':>14}")
    lines.append("─" * 70)

    for _, row in df_sorted.head(30).iterrows():
        code = str(row.get("股票代码", ""))
        name = str(row.get("股票名称", ""))
        count = row.get("上榜次数", "")

        lines.append(f"{code:<8} {name:<10} {str(count):>4} ")

    if len(df_sorted) > 30:
        lines.append(f"  ... 还有 {len(df_sorted) - 30} 只未显示")

    return "\n".join(lines)
