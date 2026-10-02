"""Deterministic illustrative data. No live market data is claimed."""
import math
import random
from datetime import date, timedelta


STOCKS = [
    {"code": "600519", "name": "贵州茅台", "initials": "GZMT", "exchange": "SH", "industry": "白酒", "price": 1486.50, "change": 1.28, "bull_ratio": 68},
    {"code": "300750", "name": "宁德时代", "initials": "NDSD", "exchange": "SZ", "industry": "新能源", "price": 258.36, "change": -0.82, "bull_ratio": 57},
    {"code": "688981", "name": "中芯国际", "initials": "ZXGJ", "exchange": "SH", "industry": "半导体", "price": 92.18, "change": 2.46, "bull_ratio": 76},
    {"code": "000001", "name": "平安银行", "initials": "PAYH", "exchange": "SZ", "industry": "银行", "price": 11.63, "change": 0.52, "bull_ratio": 62},
    {"code": "002594", "name": "比亚迪", "initials": "BYD", "exchange": "SZ", "industry": "汽车", "price": 106.28, "change": -1.16, "bull_ratio": 59},
]

NEWS = {
    "600519": [
        ("渠道调研：高端白酒终端需求呈现温和修复", "示例行业快讯", "渠道样本显示，部分地区高端白酒终端动销较上月改善，库存周转有所加快。该信息属于演示材料，未经真实渠道核验。", 1, "消费复苏"),
        ("公司经营观察：海外市场拓展带来新增量", "示例公司资讯", "一家白酒企业披露海外经销网络拓展计划，拟扩大重点地区覆盖。实际收入贡献与执行节奏尚需观察。本条为虚构演示新闻。", 1, "海外布局"),
        ("行业周报：关注节后库存与批发价格变化", "示例研究观点", "研究观点认为，白酒行业短期需关注库存消化和批发价格走势，不同渠道表现存在分化。本条仅用于演示信息卡片。", 0, "行业观察"),
    ],
    "300750": [
        ("动力电池观察：储能需求为行业带来新机会", "示例行业快讯", "虚构调研显示储能项目需求增加，电池供应商可能获得新增订单；项目交付和盈利水平仍存在不确定性。", 1, "储能需求"),
        ("原材料价格波动，电池产业链关注成本传导", "示例研究观点", "虚构新闻：上游材料报价出现波动，产业链企业的盈利表现将取决于合同价格与成本传导能力。", -1, "成本变化"),
        ("公司技术开放日：新一代电池研发进度披露", "示例公司资讯", "虚构资讯：公司介绍电池研发进度，目前仍处于验证阶段，量产规模和时间未确定。", 0, "技术研发"),
    ],
}


def stock_by_code(code):
    return next((stock for stock in STOCKS if stock["code"] == code), None)


def candles(stock):
    rng = random.Random(stock["code"])
    days = []
    day = date(2026, 9, 30)
    while len(days) < 90:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    days.reverse()
    prices = []
    last = stock["price"] * 0.93
    for i, day in enumerate(days):
        opening = last * (1 + rng.uniform(-0.003, 0.003))
        closing = opening * (1 + rng.uniform(-0.011, 0.014) + math.sin(i / 7) * 0.002)
        high = max(opening, closing) * (1 + rng.uniform(0.002, 0.009))
        low = min(opening, closing) * (1 - rng.uniform(0.002, 0.009))
        prices.append({"date": day, "open": opening, "close": closing, "low": low, "high": high, "volume": rng.randint(800, 3000)})
        last = closing
    factor = stock["price"] / prices[-1]["close"]
    for candle in prices:
        for key in ("open", "close", "low", "high"):
            candle[key] = round(candle[key] * factor, 2)
    return prices


def news(stock):
    entries = NEWS.get(stock["code"], [
        (f"{stock['industry']}行业观察：市场需求与经营趋势", "示例行业快讯", f"虚构资讯：{stock['industry']}行业需求温和改善，企业经营表现仍需后续数据验证。", 1, "行业需求"),
        (f"{stock['name']}：关注经营进展与市场变化", "示例公司资讯", f"这是一条关于{stock['name']}的虚构演示资讯，未提供真实财务数据或公司公告。", 0, "公司观察"),
        ("市场波动增加，短期风险偏好出现分化", "示例研究观点", "虚构观点：市场短期波动加大，部分投资者降低风险敞口。不能据此推断股票实际走势。", -1, "市场波动"),
    ])
    return [{"id": f"{stock['code']}-{i}", "title": title, "source": source, "content": content, "score": score, "tag": tag, "time": ["09:42", "09:18", "08:56"][i]} for i, (title, source, content, score, tag) in enumerate(entries)]


def dashboard(code):
    stock = stock_by_code(code)
    if not stock:
        return None
    return {
        "data_source": "demo",
        "as_of": "2026-09-30",
        "stock": stock,
        "candles": candles(stock),
        "news": news(stock),
        "market": {"temperature": 64, "indices": [
            {"name": "上证指数", "value": "3,268.42", "change": 0.68},
            {"name": "深证成指", "value": "10,486.23", "change": 1.12},
            {"name": "创业板指", "value": "2,186.57", "change": -0.34},
        ]},
        "sentiment": {"bull": stock["bull_ratio"], "bear": 100 - stock["bull_ratio"], "sample_count": 36, "keywords": ["长期价值", "需求回暖", "业绩预期", "理性观察", "逢低关注", "市场分化"]},
    }
