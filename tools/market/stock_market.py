"""
股票市场环境工具 - 供 LangGraph ReAct 智能体调用

包含：市场情绪、大盘环境

注意：本服务器 IP 受东方财富风控阻断，所有 _em 后缀的接口不可用。
已验证可用数据源：新浪（指数/个股实时）、上交所官网、腾讯（日线）

依赖声明：
  - akshare.stock_zh_index_spot_sina      — 新浪指数实时行情
  - akshare.stock_sse_summary             — 上交所概况
  - akshare.stock_sse_deal_daily          — 上交所日成交数据
"""

from langchain_core.tools import tool
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta


# ════════════════════════════════════════════════
# 辅助：判断数据是否可用
# ════════════════════════════════════════════════

_em_blocked = False  # 首次尝试失败后设为 True


def _try_em(func_name: str, *args, **kwargs):
    """尝试调用东方财富接口，失败则标记不可用"""
    global _em_blocked
    if _em_blocked:
        raise ConnectionError("东方财富接口已被服务器风控阻断")

    import time
    func = getattr(ak, func_name, None)
    if not func:
        raise ValueError(f"akshare 无 {func_name} 函数")

    try:
        return func(*args, **kwargs)
    except ConnectionError:
        _em_blocked = True
        raise


# ════════════════════════════════════════════════
# 工具3：市场情绪
# ════════════════════════════════════════════════

@tool
def get_market_sentiment() -> dict:
    """
    获取当前A股市场情绪指标（基于新浪可用数据源）

    返回:
        字典包含主要指数涨跌和市场概况
    """
    result = {}

    # 1. 主要指数行情（新浪 - 可用）
    try:
        df_index = ak.stock_zh_index_spot_sina()
        main_indexes = {"sh000001": "上证综指", "sz399001": "深证成指",
                        "sz399006": "创业板指", "sh000688": "科创50",
                        "sh000300": "沪深300"}
        index_data = {}
        for _, row in df_index.iterrows():
            code = row.get("代码", "")
            if code in main_indexes:
                change = row.get("涨跌幅", "N/A")
                index_data[main_indexes[code]] = {
                    "最新价": row.get("最新价", "N/A"),
                    "涨跌幅": f"{change}%" if change != "N/A" else "N/A",
                }
        if index_data:
            result["主要指数"] = index_data
    except Exception as e:
        result["主要指数_错误"] = str(e)

    # 2. 上交所概况
    try:
        df_sse = ak.stock_sse_summary()
        if not df_sse.empty:
            for _, row in df_sse.iterrows():
                if row.get("项目") == "平均市盈率":
                    result["上证平均市盈率"] = row.get("股票", "N/A")
                if row.get("项目") == "上市公司":
                    result["上证上市公司数"] = int(row.get("股票", 0))
    except Exception:
        pass

    # 3. 上交所日成交
    try:
        today = datetime.now().strftime("%Y%m%d")
        df_deal = ak.stock_sse_deal_daily(date=today)
        if not df_deal.empty:
            d = df_deal.iloc[0]
            result["上证成交"] = {
                "成交金额(亿)": d.get("成交金额", "N/A"),
                "成交量(亿股)": d.get("成交量", "N/A"),
            }
    except Exception:
        pass

    # 4. 情绪判断
    sentiment = "中性"
    if "主要指数" in result:
        positive = sum(1 for v in result["主要指数"].values()
                       if "涨跌幅" in v and isinstance(v["涨跌幅"], str) and "+" in str(v["涨跌幅"]))
        negative = sum(1 for v in result["主要指数"].values()
                       if "涨跌幅" in v and isinstance(v["涨跌幅"], str) and "-" in str(v["涨跌幅"]))
        if positive >= 3:
            sentiment = "偏乐观"
        elif negative >= 3:
            sentiment = "偏悲观"
    result["情绪判断"] = sentiment

    return result


# ════════════════════════════════════════════════
# 工具4：大盘环境
# ════════════════════════════════════════════════

@tool
def get_market_environment() -> dict:
    """
    获取当前A股大盘整体环境，包含指数行情、上交所概况、市场热度。

    返回:
        字典包含大盘环境各项指标
    """
    result = {
        "时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "数据说明": "本服务器受东方财富风控限制，部分数据通过新浪和上交所官网获取",
    }

    # 1. 大盘指数行情（新浪）
    try:
        df_index = ak.stock_zh_index_spot_sina()
        main_indexes = {
            "sh000001": "上证综指", "sz399001": "深证成指",
            "sz399006": "创业板指", "sh000688": "科创50",
            "sh000300": "沪深300",
        }
        index_data = {}
        for _, row in df_index.iterrows():
            code = row.get("代码", "")
            if code in main_indexes:
                index_data[main_indexes[code]] = {
                    "最新价": row.get("最新价", "N/A"),
                    "涨跌额": row.get("涨跌额", "N/A"),
                    "涨跌幅": f"{row.get('涨跌幅', 'N/A')}%",
                }
        if index_data:
            result["指数行情"] = index_data
    except Exception as e:
        result["指数行情_错误"] = str(e)

    # 2. 上交所概况
    try:
        df_sse = ak.stock_sse_summary()
        if not df_sse.empty:
            overview = {}
            for _, row in df_sse.iterrows():
                project = row.get("项目", "")
                if project in ["总市值", "平均市盈率", "上市公司", "流通市值", "流通股本", "总股本"]:
                    overview[project] = row.get("股票", "N/A")
            if overview:
                result["上交所概况"] = overview
    except Exception as e:
        result["上交所概况_错误"] = str(e)

    # 3. 成交数据
    try:
        today = datetime.now().strftime("%Y%m%d")
        df_deal = ak.stock_sse_deal_daily(date=today)
        if not df_deal.empty:
            d = df_deal.iloc[0]
            result["当日成交详情"] = {
                "上证成交金额(亿)": d.get("成交金额", "N/A"),
                "上证成交量(亿股)": d.get("成交量", "N/A"),
                "上证平均市盈率": d.get("平均市盈率", "N/A"),
                "上证换手率(%)": d.get("换手率", "N/A"),
            }
    except Exception:
        pass

    # 4. 市场热度指数：简单根据换手率判断
    try:
        today = datetime.now().strftime("%Y%m%d")
        df_deal = ak.stock_sse_deal_daily(date=today)
        if not df_deal.empty:
            turnover = df_deal.iloc[0].get("换手率", 0)
            try:
                t = float(turnover)
                if t > 3:
                    result["市场热度"] = "活跃"
                elif t > 1.5:
                    result["市场热度"] = "正常"
                else:
                    result["市场热度"] = "低迷"
            except (ValueError, TypeError):
                pass
    except Exception:
        pass

    return result
