"""
股票板块工具 - 行业板块排名、领涨股、区间行情

依赖声明：
  - akshare.stock_sector_spot              — 新浪行业板块行情
  - akshare.stock_sector_detail            — 新浪行业板块成分股
  - akshare.stock_board_industry_name_ths  — 同花顺行业板块名称
  - akshare.stock_board_industry_index_ths — 同花顺行业板块指数历史
"""

from langchain_core.tools import tool


# ════════════════════════════════════════════════
# 工具1：行业板块涨跌幅排名
# ════════════════════════════════════════════════

@tool
def get_sector_rankings(top_n: int = 10) -> str:
    """
    获取A股行业板块涨跌幅排名（数据源：新浪）。

    按今日涨跌幅从高到低排序，查看哪些板块表现最好、哪些最差。

    参数:
        top_n: 返回前多少只板块，默认 10

    返回:
        行业板块排名（板块名 + 涨跌幅 + 总成交额 + 上涨家数/下跌家数）
    """
    import akshare as ak

    df = ak.stock_sector_spot(indicator="新浪行业")
    if df.empty or "板块" not in df.columns:
        return "当前无法获取行业板块数据"

    df_sorted = df.sort_values("涨跌幅", ascending=False)

    n = min(top_n, len(df_sorted))
    lines = [f"📊 行业板块涨跌排名（共 {len(df_sorted)} 个板块）", ""]
    lines.append(f"{'排名':>4} {'板块':<12} {'涨跌幅':>8} {'总成交额':>14} {'均价':>8} {'上涨':>4}/{('下跌'):>4}")
    lines.append("─" * 60)

    for i, (_, row) in enumerate(df_sorted.head(n).iterrows(), 1):
        name = str(row.get("板块", ""))
        pct = row.get("涨跌幅", "")
        amount = row.get("总成交额", 0)
        price = row.get("平均价格", "")
        up = row.get("上涨家数", "")
        down = row.get("下跌家数", "")
        lines.append(f"{i:>4} {name:<12} {str(pct):>8} {str(amount):>14} {str(price):>8} {str(up):>4}/{str(down):>4}")

    return "\n".join(lines)


# ════════════════════════════════════════════════
# 工具2：板块领涨股
# ════════════════════════════════════════════════

@tool
def get_sector_leaders(sector_name: str, top_n: int = 10, sort_by: str = "涨跌幅") -> str:
    """
    获取指定行业板块中排名靠前的成分股，可按不同维度排序。

    先通过关键词匹配板块名称，再拉取该板块下所有股票按指定维度降序排列。

    可用行业板块：环保、汽车制造、电子器件、电力、化工、有色金属、酿酒等 49 个新浪行业分类。

    参数:
        sector_name: 行业板块名称或关键词，如 "环保"、"汽车"、"电子器件"
        top_n: 返回前多少只，默认 10
        sort_by: 排序维度，可选 "涨跌幅"（默认）、"成交额"、"换手率"、"市值"

    返回:
        板块成分股排名列表
    """
    import akshare as ak

    # 排序字段映射
    sort_map = {
        "涨跌幅": "changepercent",
        "涨幅": "changepercent",
        "成交额": "amount",
        "金额": "amount",
        "换手率": "turnoverratio",
        "换手": "turnoverratio",
        "市值": "mktcap",
        "总市值": "mktcap",
    }

    field = sort_map.get(sort_by)
    if not field:
        return (f"不支持的排序方式 '{sort_by}'，可选："
                f"{'、'.join(sort_map.keys())}")

    # 1. 获取行业板块映射
    df_spot = ak.stock_sector_spot(indicator="新浪行业")
    if df_spot.empty or "板块" not in df_spot.columns:
        return "当前无法获取行业板块列表"

    # 2. 按名称匹配
    matched = df_spot[df_spot["板块"].str.contains(sector_name, na=False)]
    if matched.empty:
        return f"未找到名称包含 '{sector_name}' 的行业板块"

    sector_label = matched.iloc[0]["label"]
    sector_display = matched.iloc[0]["板块"]

    # 3. 获取成分股
    df_cons = ak.stock_sector_detail(sector=sector_label)
    if df_cons.empty:
        return f"板块 '{sector_display}' 暂无成分股数据"

    # 4. 按指定维度降序
    df_sorted = df_cons.sort_values(field, ascending=False)

    n = min(top_n, len(df_sorted))

    # 表头
    dim_label = sort_by
    lines = [f"📊 板块: {sector_display}  |  共 {len(df_sorted)} 只成分股  |  按{dim_label}排序", ""]
    lines.append(f"{'排名':>4} {'代码':<8} {'名称':<10} {'最新价':>8} {'涨跌幅':>8}  {'涨跌额':>8}  {'成交额':>12}  {'总市值(亿)':>10}")
    lines.append("─" * 82)

    for i, (_, row) in enumerate(df_sorted.head(n).iterrows(), 1):
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        trade = row.get("trade", "")
        pct = row.get("changepercent", "")
        chg = row.get("pricechange", "")
        amount = row.get("amount", 0)
        mktcap = row.get("mktcap", 0)
        try:
            mktcap_yi = round(float(mktcap) / 10000, 1)
            amount_yi = round(float(amount) / 10000, 1)
        except (ValueError, TypeError):
            mktcap_yi = ""
            amount_yi = ""
        lines.append(f"{i:>4} {code:<8} {name:<10} {str(trade):>8} {str(pct):>8}  {str(chg):>8}  {str(amount_yi):>12}  {str(mktcap_yi):>10}")

    if len(df_sorted) > n:
        lines.append(f"  ... 还有 {len(df_sorted) - n} 只未显示（可用 top_n 参数增加）")

    return "\n".join(lines)


# ════════════════════════════════════════════════
# 工具3：行业板块区间行情
# ════════════════════════════════════════════════

@tool
def get_sector_history(sector_name: str, days: int = 30) -> str:
    """
    获取指定行业板块或概念板块在最近一段时间的走势数据（数据源：同花顺）。

    包含每日的开盘价、最高价、最低价、收盘价、成交量、成交额，
    可用于判断板块在一段时间内的涨跌趋势。

    支持两种板块：
      - 行业板块（90 个）：半导体、白酒、电池、电力、汽车整车等
      - 概念板块（374 个）：锂电池概念、人工智能、军工、国企改革等
    先搜行业板块，未命中则搜概念板块。

    参数:
        sector_name: 板块名称或关键词，如 "半导体"、"白酒"、"锂电池概念"、"人工智能"
        days: 回溯天数，默认 30 天

    返回:
        板块历史走势（日期 + 开盘 + 最高 + 最低 + 收盘 + 涨跌幅）
    """
    import akshare as ak
    from datetime import datetime, timedelta

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    # 1. 先搜行业板块（90 个）
    df_industry = ak.stock_board_industry_name_ths()
    matched = df_industry[df_industry["name"].str.contains(sector_name, na=False)]

    if not matched.empty:
        board_name = matched.iloc[0]["name"]
        df = ak.stock_board_industry_index_ths(
            symbol=board_name, start_date=start_date, end_date=end_date,
        )
        board_type = "行业板块"
    else:
        # 2. 未命中则搜概念板块（374 个）
        df_concept = ak.stock_board_concept_name_ths()
        matched = df_concept[df_concept["name"].str.contains(sector_name, na=False)]

        if matched.empty:
            sample = df_industry["name"].head(20).tolist()
            return (f"未找到名称包含 '{sector_name}' 的板块。\n"
                    f"示例板块：{', '.join(sample)}\n"
                    f"（共 464 个行业+概念板块，可使用更精确的关键词重试）")

        board_name = matched.iloc[0]["name"]
        df = ak.stock_board_concept_index_ths(
            symbol=board_name, start_date=start_date, end_date=end_date,
        )
        board_type = "概念板块"

    if df.empty:
        return f"{board_type} '{board_name}' 暂无历史数据"

    # 3. 计算每日涨跌幅
    df = df.copy()
    df["涨跌幅"] = df["收盘价"].pct_change() * 100

    lines = [f"📊 {board_type}: {board_name} 近 {len(df)} 个交易日走势", ""]
    lines.append(f"{'日期':<12} {'开盘':>10} {'最高':>10} {'最低':>10} {'收盘':>10} {'涨跌幅':>8}")
    lines.append("─" * 65)

    for _, row in df.iterrows():
        date = str(row.get("日期", ""))[:10]
        o = row.get("开盘价", "")
        h = row.get("最高价", "")
        l = row.get("最低价", "")
        c = row.get("收盘价", "")
        pct = row.get("涨跌幅", 0)
        try:
            pct_val = float(pct)
            pct_str = f"{pct_val:+.2f}%" if not (pct_val != pct_val) else "   —  "
        except (ValueError, TypeError):
            pct_str = str(pct)
        lines.append(f"{date:<12} {float(o):>10.2f} {float(h):>10.2f} {float(l):>10.2f} {float(c):>10.2f} {pct_str:>8}")

    return "\n".join(lines)
