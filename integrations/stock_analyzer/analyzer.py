import math
import os
import re
import sys
import time

import pandas as pd
import requests


DEFAULT_FORECAST_WINDOW_DAYS = 180
FINANCE_BASE_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis"


def _to_float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _extract_api_rows(payload):
    """兼容东方财富财报接口的两种常见 JSON 包装。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        return result["data"]
    return []


def determine_suffix(stock_code):
    """根据股票代码自动判断后缀:6开头为SH,0/3开头为SZ"""
    if stock_code.startswith('6'):
        return 'SH'
    elif stock_code.startswith('0') or stock_code.startswith('3'):
        return 'SZ'
    else:
        raise ValueError(f"不支持的股票代码: {stock_code},仅支持 0/3/6 开头的A股。")


def prepare_forecasts(rows, window_days=DEFAULT_FORECAST_WINDOW_DAYS):
    """保留近期预测，并为每家机构保留最新的一份报告。"""
    if not rows:
        raise ValueError("未获取到任何预测数据")
    if not isinstance(window_days, int) or window_days <= 0:
        raise ValueError("预测时间窗口必须是正整数天数")

    df = pd.DataFrame(rows)
    if "YEAR1" not in df.columns:
        raise ValueError("预测数据缺少 YEAR1")
    if "PUBLISH_DATE" not in df.columns:
        raise ValueError("预测数据缺少 PUBLISH_DATE")
    df["_publish_date"] = pd.to_datetime(df["PUBLISH_DATE"], errors="coerce")
    valid_dates = df["_publish_date"].dropna()
    if valid_dates.empty:
        raise ValueError("无法解析预测数据中的 PUBLISH_DATE")
    latest_report_date = valid_dates.max()
    window_start_date = latest_report_date - pd.Timedelta(days=window_days)
    df = df[df["_publish_date"] >= window_start_date].copy()

    years = pd.to_numeric(df["YEAR1"], errors="coerce").dropna()
    if years.empty:
        raise ValueError("无法解析预测数据中的 YEAR1")
    base_year = int(years.mode().iloc[0])
    df = df[pd.to_numeric(df["YEAR1"], errors="coerce") == base_year].copy()

    if "ORG_CODE" in df.columns:
        institution_key = df["ORG_CODE"].astype("string")
    else:
        institution_key = pd.Series(pd.NA, index=df.index, dtype="string")
    if "ORG_NAME_ABBR" in df.columns:
        institution_key = institution_key.fillna(df["ORG_NAME_ABBR"].astype("string"))
    institution_key = institution_key.fillna(pd.Series(df.index.astype(str), index=df.index))
    df["_institution_key"] = institution_key
    df = (
        df.sort_values("_publish_date", ascending=False, na_position="last")
        .drop_duplicates("_institution_key", keep="first")
    )

    column_mapping = {
        "SECURITY_NAME_ABBR": "股票名称",
        "ORG_NAME_ABBR": "机构名称",
        "RESEARCHER": "研究员",
        "PUBLISH_DATE": "报告日期",
        "EPS1": f"{base_year}_每股收益(元)",
        "PARENT_NETPROFIT1": f"{base_year}_净利润(元)",
        "EPS2": f"{base_year + 1}E_预测每股收益(元)",
        "PARENT_NETPROFIT2": f"{base_year + 1}E_预测净利润(元)",
        "EPS3": f"{base_year + 2}E_预测每股收益(元)",
        "PARENT_NETPROFIT3": f"{base_year + 2}E_预测净利润(元)",
        "EPS4": f"{base_year + 3}E_预测每股收益(元)",
        "PARENT_NETPROFIT4": f"{base_year + 3}E_预测净利润(元)",
        "RATING": "投资评级",
    }
    df = df.rename(columns=column_mapping)
    if "报告日期" in df.columns:
        df["报告日期"] = df["报告日期"].astype("string").str[:10]

    value_columns = [
        column
        for column in df.columns
        if "净利润(元)" in column or "每股收益(元)" in column
    ]
    for column in value_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    final_columns = [
        "机构名称", "研究员",
        f"{base_year}_每股收益(元)", f"{base_year}_净利润(元)",
        f"{base_year + 1}E_预测每股收益(元)", f"{base_year + 1}E_预测净利润(元)",
        f"{base_year + 2}E_预测每股收益(元)", f"{base_year + 2}E_预测净利润(元)",
        f"{base_year + 3}E_预测每股收益(元)", f"{base_year + 3}E_预测净利润(元)",
        "投资评级", "报告日期",
    ]
    result = df[[column for column in final_columns if column in df.columns]].reset_index(drop=True)
    result.attrs.update(
        window_days=window_days,
        window_start_date=window_start_date.strftime("%Y-%m-%d"),
        latest_report_date=latest_report_date.strftime("%Y-%m-%d"),
    )
    return result

def get_stock_price_name_pe(stock_code):
    """获取股票名称、最新价、市盈率 TTM 和总市值。"""
    market = '1' if stock_code.startswith('6') else '0'
    secid = f"{market}.{stock_code}"
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "fields": "f58,f43,f164,f20,f116",
        "secid": secid,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "wbp2u": "|0|0|0|web",
        "v": "05357864115986435"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    last_error = None
    for attempt in range(2):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=8)
            response.raise_for_status()
            data = response.json()
            result = data.get("data", {})
            name = result.get("f58")
            price = _to_float(result.get("f43"))
            pe_ttm = _to_float(result.get("f164"))  # 市盈率TTM
            market_cap = _to_float(result.get("f116") or result.get("f20"))
            if not name or price is None or pe_ttm is None or market_cap is None:
                last_error = ValueError("东财行情关键字段缺失")
                break
            return name, price, pe_ttm, market_cap
        except Exception as error:
            last_error = error
            if attempt < 1:
                time.sleep(0.5)

    market_prefix = "sh" if stock_code.startswith("6") else "sz"
    tencent_error = None
    for t_attempt in range(2):
        try:
            fallback_response = requests.get(
                f"https://qt.gtimg.cn/q={market_prefix}{stock_code}",
                headers={"User-Agent": headers["User-Agent"]},
                timeout=8,
            )
            fallback_response.raise_for_status()
            fallback_response.encoding = "gbk"
            match = re.search(r'="(.*)";', fallback_response.text)
            if not match:
                raise ValueError("腾讯行情响应格式异常")
            fields = match.group(1).split("~")
            if len(fields) <= 45:
                raise ValueError("腾讯行情字段不完整")
            name = fields[1] or None
            price = _to_float(fields[3])
            pe_ttm = _to_float(fields[39])
            market_cap_yi = _to_float(fields[45])
            market_cap = market_cap_yi * 100_000_000 if market_cap_yi is not None else None
            return name, price, pe_ttm, market_cap
        except Exception as fallback_error:
            tencent_error = fallback_error
            if t_attempt < 1:
                time.sleep(0.5)

    print(f"获取实时行情失败: 东财 {last_error}; 腾讯 {tencent_error}")
    return None, None, None, None

def fetch_profit_forecast(
    stock_code,
    stock_suffix,
    window_days=None,
):
    """抓取全部分页的盈利预测，并只保留近期机构报告。"""
    if window_days is None:
        window_days = DEFAULT_FORECAST_WINDOW_DAYS
    base_url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    columns = (
        "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,SECURITY_INNER_CODE,"
        "PUBLISH_DATE,ORG_CODE,ORG_NAME_ABBR,RESEARCHER,"
        "YEAR1,YEAR_MARK1,EPS1,PARENT_NETPROFIT1,"
        "YEAR2,YEAR_MARK2,EPS2,PARENT_NETPROFIT2,"
        "YEAR3,YEAR_MARK3,EPS3,PARENT_NETPROFIT3,"
        "YEAR4,YEAR_MARK4,EPS4,PARENT_NETPROFIT4,"
        "RATING"
    )

    secucode = f"{stock_code}.{stock_suffix}"
    filter_param = f'(SECUCODE="{secucode}")'
    base_params = {
        "reportName": "RPT_HSF10_RES_PREDICTDETAIL",
        "columns": columns,
        "filter": filter_param,
        "pageSize": 200,
        "source": "HSF10",
        "client": "PC",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://emweb.securities.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        rows = []
        page_number = 1
        total_pages = 1
        while page_number <= total_pages:
            params = {**base_params, "pageNumber": page_number}
            response = requests.get(
                base_url,
                params=params,
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            result = data.get("result")
            if result is None:
                print("API 响应中没有 'result' 字段。")
                return None

            total_pages = int(result.get("pages") or 0)
            if total_pages == 0:
                print("API 返回的页数为 0，可能没有数据或请求参数错误。")
                return None
            rows.extend(result.get("data") or [])
            page_number += 1

        if not rows:
            print("未获取到任何数据行。")
            return None

        forecasts = prepare_forecasts(rows, window_days=window_days)
        print(
            f"成功获取 {len(rows)} 条原始预测；"
            f"近 {window_days} 天保留 {len(forecasts)} 家机构。"
        )
        return forecasts

    except requests.exceptions.JSONDecodeError as e:
        print(f"解析JSON时发生错误: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"网络请求发生错误: {e}")
        return None
    except Exception as e:
        print(f"发生未知错误: {e}")
        return None

def get_dividend_buyback_metrics(stock_code, stock_suffix):
    """
    获取近三年现金分红回购总额、年均净利润及其比例。
    """
    secucode = f"{stock_code}.{stock_suffix}"
    url = (
        "https://datacenter.eastmoney.com/securities/api/data/v1/get?"
        "reportName=RPT_F10_DIVIDEND_3YEAR"
        "&columns=SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,DIVIDEND_REPO,AVG_NET_PROFIT,RATIO"
        f"&filter=(SECUCODE%3D%22{secucode}%22)"
        "&pageNumber=1&pageSize=1"
        "&source=HSF10&client=PC"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://emweb.securities.eastmoney.com/PC_HSF10/Dividend/detail?type=web&code={stock_suffix}{stock_code}"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("result") and data["result"].get("data"):
            row = data["result"]["data"][0]
            return {
                "total_return": _to_float(row.get("DIVIDEND_REPO")),
                "average_net_profit": _to_float(row.get("AVG_NET_PROFIT")),
                "ratio": _to_float(row.get("RATIO")),
            }
        else:
            print("分红回购数据: 无相关数据")
            return None
    except Exception as e:
        print(f"获取分红回购数据失败: {e}")
        return None


def get_dividend_buyback_ratio(stock_code, stock_suffix):
    """兼容旧调用：仅返回近三年现金分红回购/净利比率。"""
    metrics = get_dividend_buyback_metrics(stock_code, stock_suffix)
    if not metrics:
        return None
    ratio = metrics.get("ratio")
    return None if ratio is None else round(ratio, 2)


def _cash_dividend_per_share(profile):
    """从“10派X元”等实施方案中解析每股现金分红。"""
    if not profile:
        return None
    # 模式1：标准“每X股...派Y元”或“X[股]...派Y元”
    # 中间仅允许送股/转增股说明，避免匹配到前缀中的年份、季度、日期等无关数字
    match = re.search(
        r"(?:每\s*)?(\d+(?:\.\d+)?)\s*股?"
        r"(?:(?:送|转增?)\s*[\d.]+\s*股?)*"
        r"\s*派(?:发)?(?:现金)?(?:红利)?\s*([\d.]+)\s*元",
        str(profile),
    )
    if match:
        base_shares = _to_float(match.group(1))
        cash_amount = _to_float(match.group(2))
        if base_shares and cash_amount is not None:
            return cash_amount / base_shares

    # 模式2：直接“每股派X元”
    match_per_share = re.search(
        r"每股\s*派(?:发)?(?:现金)?(?:红利)?\s*([\d.]+)\s*元",
        str(profile),
    )
    if match_per_share:
        return _to_float(match_per_share.group(1))

    return None


def calculate_shareholder_return_metrics(
    dividend_rows,
    repurchase_rows,
    equity_rows,
    latest_price,
    market_cap,
    pe_ttm,
    cagr_percent,
    growth_basis="net_profit",
    as_of_date=None,
):
    """计算近 365 天现金股息与经注销、稀释校正后的回购收益率。"""
    as_of = pd.Timestamp(as_of_date or pd.Timestamp.today()).normalize()
    window_start = as_of - pd.Timedelta(days=365)

    dividend_source_available = dividend_rows is not None
    repurchase_source_available = repurchase_rows is not None
    equity_source_available = equity_rows is not None

    cash_dividend_per_share = 0.0 if dividend_source_available else None
    dividend_event_count = 0
    for row in dividend_rows or []:
        ex_date = pd.to_datetime(row.get("EX_DIVIDEND_DATE"), errors="coerce")
        if pd.isna(ex_date) or not (window_start <= ex_date.normalize() <= as_of):
            continue
        if row.get("ASSIGN_PROGRESS") != "实施方案" or str(row.get("IS_UNASSIGN") or "0") == "1":
            continue
        per_share = _cash_dividend_per_share(row.get("IMPL_PLAN_PROFILE"))
        if per_share is not None and per_share >= 0:
            cash_dividend_per_share += per_share
            dividend_event_count += 1

    price = _to_float(latest_price)
    cash_dividend_yield = (
        cash_dividend_per_share / price * 100
        if cash_dividend_per_share is not None and price and price > 0
        else None
    )

    cancelled_buyback_amount = 0.0 if repurchase_source_available else None
    cancelled_buyback_count = 0
    for row in repurchase_rows or []:
        finish_date = pd.to_datetime(row.get("FINISHDATE"), errors="coerce")
        if pd.isna(finish_date) or not (window_start <= finish_date.normalize() <= as_of):
            continue
        objective = str(row.get("REPUROBJECTIVE") or "")
        progress = str(row.get("REPURPROGRESS") or "")
        amount = _to_float(row.get("REPURAMOUNT"), _to_float(row.get("ZJJE"), 0))
        is_incentive_purpose = any(
            keyword in objective for keyword in ("员工持股", "股权激励", "员工激励", "奖励")
        )
        if (
            progress == "006"
            and "注销" in objective
            and not is_incentive_purpose
            and amount > 0
        ):
            cancelled_buyback_amount += amount
            cancelled_buyback_count += 1

    market_cap_value = _to_float(market_cap)
    raw_buyback_yield = (
        cancelled_buyback_amount / market_cap_value * 100
        if cancelled_buyback_amount is not None and market_cap_value and market_cap_value > 0
        else None
    )

    dated_equity = []
    for row in equity_rows or []:
        change_date = pd.to_datetime(row.get("END_DATE"), errors="coerce")
        shares = _to_float(row.get("TOTAL_SHARES"))
        if not pd.isna(change_date) and shares and shares > 0 and change_date.normalize() <= as_of:
            dated_equity.append((change_date.normalize(), shares))
    dated_equity.sort(key=lambda item: item[0], reverse=True)
    current_shares = dated_equity[0][1] if dated_equity else None
    prior_candidates = [item for item in dated_equity if item[0] <= window_start]
    prior_shares = prior_candidates[0][1] if prior_candidates else None
    net_share_reduction = None
    if current_shares and prior_shares:
        net_share_reduction = (prior_shares - current_shares) / prior_shares * 100

    effective_buyback_yield = None
    if cancelled_buyback_amount == 0 and repurchase_source_available:
        effective_buyback_yield = 0.0
    elif raw_buyback_yield is not None and net_share_reduction is not None:
        effective_buyback_yield = min(raw_buyback_yield, max(net_share_reduction, 0.0))

    basis = str(growth_basis).lower()
    buyback_yield_used = effective_buyback_yield
    if basis == "eps":
        buyback_yield_used = 0.0
        buyback_note = "增长口径为 EPS，回购造成的股数下降可能已计入增长，未重复加入"
    elif not repurchase_source_available:
        buyback_note = "回购数据获取失败，回购收益率未计入"
    elif cancelled_buyback_amount and not equity_source_available:
        buyback_note = "股本数据获取失败，回购收益率未计入"
    elif net_share_reduction is None and cancelled_buyback_amount > 0:
        buyback_yield_used = 0.0
        buyback_note = "缺少同期股本数据，回购收益率按 0 计入"
    elif net_share_reduction is not None and net_share_reduction <= 0 and cancelled_buyback_amount > 0:
        buyback_yield_used = 0.0
        buyback_note = "同期股本未减少或发生稀释，回购收益率按 0 计入"
    elif cancelled_buyback_amount <= 0:
        buyback_note = "近 365 天无已完成且明确注销的回购"
    else:
        buyback_note = "回购收益率已按净股本减少率设置上限"

    shareholder_return_yield = None
    if cash_dividend_yield is not None and buyback_yield_used is not None:
        shareholder_return_yield = cash_dividend_yield + buyback_yield_used

    pe_value = _to_float(pe_ttm)
    growth = _to_float(cagr_percent)
    shareholder_adjusted_peg = None
    if (
        pe_value
        and pe_value > 0
        and growth is not None
        and shareholder_return_yield is not None
        and growth + shareholder_return_yield > 0
    ):
        shareholder_adjusted_peg = pe_value / (growth + shareholder_return_yield)

    cash_dividend_amount = (
        cash_dividend_per_share * current_shares
        if cash_dividend_per_share is not None and current_shares is not None
        else None
    )
    return {
        "window_start_date": window_start.strftime("%Y-%m-%d"),
        "window_end_date": as_of.strftime("%Y-%m-%d"),
        "growth_basis": basis,
        "cash_dividend_per_share_ttm": cash_dividend_per_share,
        "cash_dividend_yield_percent": cash_dividend_yield,
        "dividend_event_count": dividend_event_count,
        "cash_dividend_amount_ttm": cash_dividend_amount,
        "cancelled_buyback_amount_ttm": cancelled_buyback_amount,
        "cancelled_buyback_count": cancelled_buyback_count,
        "raw_buyback_yield_percent": raw_buyback_yield,
        "net_share_reduction_percent": net_share_reduction,
        "effective_buyback_yield_percent": effective_buyback_yield,
        "buyback_yield_used_percent": buyback_yield_used,
        "buyback_adjustment_note": buyback_note,
        "shareholder_return_yield_percent": shareholder_return_yield,
        "shareholder_adjusted_peg": shareholder_adjusted_peg,
        "current_total_shares": current_shares,
        "prior_total_shares": prior_shares,
    }


def _fetch_paginated_rows(url, params, headers):
    rows = []
    page_number = 1
    total_pages = 1
    while page_number <= total_pages:
        response = requests.get(
            url,
            params={**params, "pageNumber": page_number},
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if (
            isinstance(payload, dict)
            and payload.get("code") == 9201
            and "空" in str(payload.get("message") or "")
        ):
            return rows
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise ValueError("上游接口返回错误状态")
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ValueError("上游接口响应结构异常")
        rows.extend(result["data"])
        total_pages = int(result.get("pages") or 1)
        page_number += 1
    return rows


def fetch_shareholder_return_data(stock_code, stock_suffix):
    """分别获取分红实施、回购执行和历史股本数据。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://emweb.securities.eastmoney.com/",
    }
    secucode = f"{stock_code}.{stock_suffix}"
    securities_url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    repurchase_url = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    requests_to_make = (
        (
            "分红明细",
            securities_url,
            {
                "reportName": "RPT_F10_DIVIDEND_MAIN",
                "columns": (
                    "SECUCODE,ASSIGN_PROGRESS,EX_DIVIDEND_DATE,IS_UNASSIGN,"
                    "IMPL_PLAN_PROFILE,REPORT_DATE,TOTAL_DIVIDEND_A"
                ),
                "filter": f'(SECUCODE="{secucode}")',
                "pageSize": 200,
                "sortTypes": -1,
                "sortColumns": "NOTICE_DATE",
                "source": "HSF10",
                "client": "PC",
            },
        ),
        (
            "回购明细",
            repurchase_url,
            {
                "reportName": "RPTA_WEB_GETHGLIST_NEW",
                "columns": "ALL",
                "filter": f'(DIM_SCODE="{stock_code}")',
                "pageSize": 200,
                "sortColumns": "UPD,DIM_DATE,DIM_SCODE",
                "sortTypes": "-1,-1,-1",
                "source": "WEB",
                "client": "WEB",
            },
        ),
        (
            "股本结构",
            securities_url,
            {
                "reportName": "RPT_F10_EH_EQUITY",
                "columns": "SECUCODE,END_DATE,TOTAL_SHARES,CHANGE_REASON",
                "filter": f'(SECUCODE="{secucode}")',
                "pageSize": 500,
                "sortTypes": -1,
                "sortColumns": "END_DATE",
                "source": "HSF10",
                "client": "PC",
            },
        ),
    )

    results = []
    for label, url, params in requests_to_make:
        try:
            results.append(_fetch_paginated_rows(url, params, headers))
        except Exception as error:
            print(f"获取{label}失败: {error}")
            results.append(None)
    return tuple(results)


def _get_company_type(stock_code, stock_suffix, headers):
    """从财务分析页读取东方财富内部的公司类型编号。"""
    response = requests.get(
        f"{FINANCE_BASE_URL}/Index",
        params={"type": "web", "code": f"{stock_suffix}{stock_code}".lower()},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    tag = re.search(r"<[^>]*\bid=[\"']hidctype[\"'][^>]*>", response.text, re.I)
    if not tag:
        raise ValueError("无法识别财报公司类型")
    value = re.search(r"\bvalue=[\"']([^\"']+)", tag.group(0), re.I)
    if not value:
        raise ValueError("财报公司类型为空")
    return value.group(1)


def fetch_cash_return_statements(stock_code, stock_suffix):
    """获取最近三个完整年度现金流量表，以及最新年度资产负债表。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://emweb.securities.eastmoney.com/",
    }
    symbol = f"{stock_suffix}{stock_code}"
    try:
        company_type = _get_company_type(stock_code, stock_suffix, headers)
        common_params = {
            "companyType": company_type,
            "reportDateType": 1,
            "code": symbol,
        }
        date_response = requests.get(
            f"{FINANCE_BASE_URL}/xjllbDateAjaxNew",
            params=common_params,
            headers=headers,
            timeout=10,
        )
        date_response.raise_for_status()
        date_rows = _extract_api_rows(date_response.json())
        report_dates = []
        for row in date_rows:
            report_date = str(row.get("REPORT_DATE") or "")[:10]
            if report_date and report_date not in report_dates:
                report_dates.append(report_date)
        report_dates = sorted(report_dates, reverse=True)[:3]
        if len(report_dates) < 3:
            raise ValueError("不足三个完整年度的现金流量表")

        cash_response = requests.get(
            f"{FINANCE_BASE_URL}/xjllbAjaxNew",
            params={
                **common_params,
                "reportType": 2,
                "dates": ",".join(report_dates),
            },
            headers=headers,
            timeout=10,
        )
        cash_response.raise_for_status()
        cash_rows = _extract_api_rows(cash_response.json())

        balance_response = requests.get(
            f"{FINANCE_BASE_URL}/zcfzbAjaxNew",
            params={
                **common_params,
                "reportType": 1,
                "dates": report_dates[0],
            },
            headers=headers,
            timeout=10,
        )
        balance_response.raise_for_status()
        balance_rows = _extract_api_rows(balance_response.json())
        if len(cash_rows) < 3 or not balance_rows:
            raise ValueError("现金流量表或资产负债表数据不完整")
        return cash_rows, balance_rows[0]
    except Exception as error:
        print(f"获取现金回报质量数据失败: {error}")
        return None, None


def calculate_cash_return_quality(
    cash_flow_rows,
    balance_row,
    dividend_metrics,
    market_cap,
    pe_ttm,
    cagr_percent,
):
    """量化现金兑现、股东回报覆盖、负债，以及两种修正 PEG。"""
    if not cash_flow_rows or not dividend_metrics:
        return None

    org_type = str(cash_flow_rows[0].get("ORG_TYPE") or "")
    if any(keyword in org_type for keyword in ("银行", "保险", "证券", "金融")):
        financial_dates = pd.to_datetime(
            [row.get("REPORT_DATE") for row in cash_flow_rows],
            errors="coerce",
        )
        valid_years = [date.year for date in financial_dates if not pd.isna(date)]
        if len(set(valid_years)) < 3:
            return None
        return {
            **dividend_metrics,
            "period_start_year": min(valid_years),
            "period_end_year": max(valid_years),
            "unsupported_reason": "金融企业不适用通用自由现金流口径",
        }

    validated_dates = []
    for row in cash_flow_rows:
        report_date = pd.to_datetime(row.get("REPORT_DATE"), errors="coerce")
        operating_cash = _to_float(row.get("NETCASH_OPERATE"))
        capital_expenditure = _to_float(row.get("CONSTRUCT_LONG_ASSET"))
        if pd.isna(report_date) or operating_cash is None or capital_expenditure is None:
            return None
        validated_dates.append(report_date.normalize())
    if len(set(validated_dates)) < 3:
        return None

    rows = sorted(
        cash_flow_rows,
        key=lambda row: pd.Timestamp(row.get("REPORT_DATE")),
        reverse=True,
    )[:3]
    if len(rows) < 3:
        return None

    operating_cash_flow_total = sum(_to_float(row.get("NETCASH_OPERATE"), 0) for row in rows)
    capital_expenditure_total = sum(
        max(_to_float(row.get("CONSTRUCT_LONG_ASSET"), 0), 0) for row in rows
    )
    net_profit_total = sum(_to_float(row.get("NETPROFIT"), 0) for row in rows)
    free_cash_flow_total = operating_cash_flow_total - capital_expenditure_total
    uses_separated_returns = "shareholder_return_yield_percent" in dividend_metrics
    total_return = _to_float(dividend_metrics.get("total_return"))
    if uses_separated_returns:
        dividend_cash = _to_float(dividend_metrics.get("cash_dividend_amount_ttm"))
        buyback_cash = _to_float(dividend_metrics.get("cancelled_buyback_amount_ttm"))
        total_return = (
            dividend_cash + buyback_cash
            if dividend_cash is not None and buyback_cash is not None
            else None
        )

    cash_conversion = (
        operating_cash_flow_total / net_profit_total if net_profit_total > 0 else None
    )
    if uses_separated_returns:
        shareholder_return_coverage = (
            free_cash_flow_total / len(rows) / total_return
            if total_return and total_return > 0
            else None
        )
    else:
        shareholder_return_coverage = (
            free_cash_flow_total / total_return if total_return and total_return > 0 else None
        )

    debt_fields = (
        "SHORT_LOAN",
        "LONG_LOAN",
        "NONCURRENT_LIAB_1YEAR",
        "BOND_PAYABLE",
        "LEASE_LIAB",
        "SHORT_BOND_PAYABLE",
        "SHORT_FIN_PAYABLE",
    )
    balance_row = balance_row or {}
    interest_bearing_debt = sum(_to_float(balance_row.get(field), 0) for field in debt_fields)
    cash = _to_float(balance_row.get("MONETARYFUNDS"), 0)
    net_debt = interest_bearing_debt - cash

    market_cap_value = _to_float(market_cap)
    pe_value = _to_float(pe_ttm)
    growth = _to_float(cagr_percent)
    if not market_cap_value and pe_value and pe_value > 0:
        average_net_profit = _to_float(dividend_metrics.get("average_net_profit"))
        if average_net_profit and average_net_profit > 0:
            market_cap_value = average_net_profit * pe_value
    payout_yield_percent = None
    free_cash_flow_yield_percent = None
    cash_supported_yield_percent = None
    payout_adjusted_peg = None
    cash_adjusted_peg = None
    if market_cap_value and market_cap_value > 0:
        annual_fcf = max(free_cash_flow_total, 0) / len(rows)
        free_cash_flow_yield_percent = annual_fcf / market_cap_value * 100
        if uses_separated_returns:
            payout_yield_percent = _to_float(
                dividend_metrics.get("shareholder_return_yield_percent")
            )
            if payout_yield_percent is not None:
                cash_supported_yield_percent = min(
                    max(payout_yield_percent, 0),
                    free_cash_flow_yield_percent,
                )
            payout_adjusted_peg = _to_float(
                dividend_metrics.get("shareholder_adjusted_peg")
            )
        else:
            if total_return is not None:
                annual_payout = max(total_return, 0) / len(rows)
                payout_yield_percent = annual_payout / market_cap_value * 100
                cash_supported_yield_percent = min(annual_payout, annual_fcf) / market_cap_value * 100
        if pe_value and pe_value > 0 and growth is not None:
            if (
                payout_adjusted_peg is None
                and payout_yield_percent is not None
                and growth + payout_yield_percent > 0
            ):
                payout_adjusted_peg = pe_value / (growth + payout_yield_percent)
            if (
                cash_supported_yield_percent is not None
                and growth + cash_supported_yield_percent > 0
            ):
                cash_adjusted_peg = pe_value / (growth + cash_supported_yield_percent)

    years = [int(str(row.get("REPORT_DATE"))[:4]) for row in rows]
    result = {
        "period_start_year": min(years),
        "period_end_year": max(years),
        "operating_cash_flow_total": operating_cash_flow_total,
        "capital_expenditure_total": capital_expenditure_total,
        "free_cash_flow_total": free_cash_flow_total,
        "cash_conversion": cash_conversion,
        "shareholder_return_coverage": shareholder_return_coverage,
        "interest_bearing_debt": interest_bearing_debt,
        "net_debt": net_debt,
        "payout_yield_percent": payout_yield_percent,
        "free_cash_flow_yield_percent": free_cash_flow_yield_percent,
        "cash_supported_yield_percent": cash_supported_yield_percent,
        "payout_adjusted_peg": payout_adjusted_peg,
        "shareholder_adjusted_peg": payout_adjusted_peg,
        "cash_adjusted_peg": cash_adjusted_peg,
    }
    if uses_separated_returns:
        result.update(dividend_metrics)
        result["free_cash_flow_yield_percent"] = free_cash_flow_yield_percent
        result["cash_supported_yield_percent"] = cash_supported_yield_percent
        result["shareholder_return_coverage"] = shareholder_return_coverage
        result["payout_adjusted_peg"] = payout_adjusted_peg
        result["shareholder_adjusted_peg"] = payout_adjusted_peg
        result["cash_adjusted_peg"] = cash_adjusted_peg
    return result


def get_historical_volatility(stock_code, stock_suffix):
    """
    获取历史波动率
    返回: 字符串如 "26.41%" 或 None
    """
    secucode = f"{stock_code}.{stock_suffix}"
    url = (
        "https://datacenter.eastmoney.com/securities/api/data/v1/get?"
        "reportName=RPT_CUSTOM_RISK_MINESWEEPER"
        f"&filter=(SECUCODE%3D%22{secucode}%22)"
        "&client=APP&source=SECURITIES&v=02145698361758448"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://emweb.securities.eastmoney.com/"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("result") and data["result"].get("data"):
            for item in data["result"]["data"]:
                risk_list = item.get("LIST", [])
                for classic in risk_list:
                    details = classic.get("DETAILS", [])
                    for detail in details:
                        if detail.get("RISK") == "历史波动率":
                            return detail.get("CONTENT_RETRACT")
            print("历史波动率: 未在DETAILS中找到相关数据")
            return None
        else:
            print("历史波动率: 无相关数据结构")
            return None
    except Exception as e:
        print(f"获取历史波动率失败: {e}")
        return None


def calculate_analysis(forecasts, pe_ttm=None, market_cap=None):
    """根据机构预测计算稳健的增长、估值与分歧指标。"""
    base_years = []
    for column in forecasts.columns:
        suffix = "_净利润(元)"
        if column.endswith(suffix):
            year = column.removesuffix(suffix)
            if year.isdigit():
                base_years.append(int(year))

    if not base_years:
        raise ValueError("无法识别最新年报年份")

    base_year = max(base_years)
    future_year = base_year + 3
    current_column = f"{base_year}_净利润(元)"
    future_column = f"{future_year}E_预测净利润(元)"
    if future_column not in forecasts.columns:
        raise ValueError(f"缺少{future_year}E预测净利润")

    current_values = pd.to_numeric(forecasts[current_column], errors="coerce").dropna()
    current_values = current_values[current_values.map(math.isfinite)]
    future_values = pd.to_numeric(forecasts[future_column], errors="coerce").dropna()
    future_values = future_values[future_values.map(math.isfinite)]
    current_net_profit = current_values.median()
    future_net_profit = future_values.median()
    if current_values.empty or current_net_profit <= 0:
        raise ValueError(f"{base_year}年净利润数据无效或非正数")
    if future_values.empty or future_net_profit <= 0:
        raise ValueError(f"{future_year}年预测净利润数据无效或非正数")

    years = future_year - base_year
    growth_multiple = _to_float(future_net_profit / current_net_profit)
    if growth_multiple is None:
        raise ValueError("净利润增长倍数超出可计算范围")
    cagr_percent = _to_float((growth_multiple ** (1 / years) - 1) * 100)
    if cagr_percent is None:
        raise ValueError("复合增长率超出可计算范围")
    future_std = _to_float(future_values.std(ddof=0))
    divergence = (
        _to_float(future_std / future_net_profit)
        if future_std is not None
        else None
    )
    future_q1 = future_values.quantile(0.25)
    future_q3 = future_values.quantile(0.75)

    report_start_date = None
    report_end_date = None
    if "报告日期" in forecasts.columns:
        report_dates = pd.to_datetime(
            forecasts.loc[future_values.index, "报告日期"],
            errors="coerce",
        ).dropna()
        if not report_dates.empty:
            report_start_date = report_dates.min().strftime("%Y-%m-%d")
            report_end_date = report_dates.max().strftime("%Y-%m-%d")

    peg = None
    pe_value = _to_float(pe_ttm)
    if pe_value is not None and pe_value > 0 and cagr_percent > 0:
        peg = _to_float(pe_value / cagr_percent)

    forward_start_year = base_year + 1
    forward_column = f"{forward_start_year}E_预测净利润(元)"
    forward_start_net_profit = None
    forward_cagr_percent = None
    forward_pe = None
    forward_peg = None
    if forward_column in forecasts.columns:
        forward_values = pd.to_numeric(
            forecasts[forward_column], errors="coerce"
        ).dropna()
        forward_values = forward_values[forward_values.map(math.isfinite)]
        if not forward_values.empty:
            forward_median = forward_values.median()
            forward_years = future_year - forward_start_year
            if forward_median > 0 and forward_years > 0:
                forward_start_net_profit = float(forward_median)
                forward_cagr_percent = _to_float((
                    (future_net_profit / forward_median) ** (1 / forward_years) - 1
                ) * 100)
                market_cap_value = _to_float(market_cap)
                if market_cap_value is not None and market_cap_value > 0:
                    forward_pe = _to_float(market_cap_value / forward_median)
                    if (
                        forward_pe is not None
                        and forward_pe > 0
                        and forward_cagr_percent is not None
                        and forward_cagr_percent > 0
                    ):
                        forward_peg = _to_float(forward_pe / forward_cagr_percent)

    implied_ttm_net_profit = None
    base_profit_gap_ratio = None
    market_cap_value = _to_float(market_cap)
    if (
        market_cap_value is not None
        and market_cap_value > 0
        and pe_value is not None
        and pe_value > 0
    ):
        implied_ttm_net_profit = _to_float(market_cap_value / pe_value)
        if implied_ttm_net_profit is not None:
            base_profit_gap_ratio = _to_float(
                implied_ttm_net_profit / current_net_profit
            )

    abnormal_reasons = []
    if growth_multiple >= 10 or cagr_percent >= 100:
        abnormal_reasons.append("历史基期与预测期利润跨度过大")
    if base_profit_gap_ratio is not None and (
        base_profit_gap_ratio >= 2 or base_profit_gap_ratio <= 0.5
    ):
        abnormal_reasons.append("基期利润与当前 TTM 利润不匹配")
    abnormal_base = bool(abnormal_reasons)
    base_warning = None
    if abnormal_base:
        base_warning = "；".join(abnormal_reasons) + "，普通 PEG 可比性较低"

    use_forward_peg = abnormal_base and forward_peg is not None
    preferred_peg = forward_peg if use_forward_peg else peg
    preferred_peg_label = "前瞻 PEG" if use_forward_peg else "PEG"

    return {
        "base_year": base_year,
        "future_year": future_year,
        "current_net_profit": float(current_net_profit),
        "future_net_profit": float(future_net_profit),
        "future_net_profit_std": future_std,
        "future_net_profit_q1": float(future_q1),
        "future_net_profit_q3": float(future_q3),
        "forecast_count": int(future_values.count()),
        "forecast_window_days": forecasts.attrs.get("window_days"),
        "report_start_date": report_start_date,
        "report_end_date": report_end_date,
        "cagr_percent": float(cagr_percent),
        "growth_multiple": growth_multiple,
        "peg": peg,
        "divergence": divergence,
        "forward_start_year": forward_start_year,
        "forward_start_net_profit": forward_start_net_profit,
        "forward_cagr_percent": (
            None if forward_cagr_percent is None else float(forward_cagr_percent)
        ),
        "forward_pe": None if forward_pe is None else float(forward_pe),
        "forward_peg": None if forward_peg is None else float(forward_peg),
        "implied_ttm_net_profit": (
            None if implied_ttm_net_profit is None else float(implied_ttm_net_profit)
        ),
        "base_profit_gap_ratio": (
            None if base_profit_gap_ratio is None else float(base_profit_gap_ratio)
        ),
        "abnormal_base": abnormal_base,
        "base_warning": base_warning,
        "preferred_peg": preferred_peg,
        "preferred_peg_label": preferred_peg_label,
        "use_forward_peg": use_forward_peg,
    }


def select_adjusted_peg_inputs(metrics, pe_ttm):
    """为股东回报与现金约束 PEG 选择一致的 PE/增速口径。"""
    if (
        metrics.get("use_forward_peg")
        and _to_float(metrics.get("forward_pe"), 0) > 0
        and _to_float(metrics.get("forward_cagr_percent"), 0) > 0
        and _to_float(metrics.get("forward_peg"), 0) > 0
    ):
        return (
            _to_float(metrics.get("forward_pe")),
            _to_float(metrics.get("forward_cagr_percent")),
            "前瞻口径",
        )
    return _to_float(pe_ttm), _to_float(metrics.get("cagr_percent")), "TTM 口径"


def _format_number(value, decimals=2, suffix=""):
    number = _to_float(value)
    if number is None:
        return "N/A"
    return f"{number:,.{decimals}f}{suffix}"


def _format_yi(value):
    number = _to_float(value)
    if number is None:
        return "N/A"
    return f"{number / 100_000_000:,.2f} 亿"


def _style(text, ansi_code):
    supports_color = (
        hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and "NO_COLOR" not in os.environ
    )
    if not supports_color:
        return text
    return f"\033[{ansi_code}m{text}\033[0m"


def print_analysis_summary(
    stock_code,
    stock_name,
    latest_price,
    pe_ttm,
    metrics,
    volatility,
    dividend_ratio,
    cash_quality=None,
    shareholder_metrics=None,
):
    """以统一格式输出分析摘要，缺失指标显示为 N/A。"""
    divider = "━" * 48
    try:
        stock_suffix = determine_suffix(stock_code)
    except ValueError:
        stock_suffix = "未知市场"

    print("\n" + _style(divider, "1;36"))
    print(_style(f"  {stock_name or '未知股票'}  {stock_code} · {stock_suffix}", "1"))
    print(_style(divider, "1;36"))

    print("\n" + _style("行情", "1;36"))
    print(f"  最新价: {_format_number(latest_price, suffix=' 元')}")
    print(f"  市盈率TTM: {_format_number(pe_ttm, suffix=' 倍')}")

    preferred_peg_label = metrics.get("preferred_peg_label", "PEG")
    preferred_peg = metrics.get("preferred_peg", metrics.get("peg"))
    peg_text = f"★  {preferred_peg_label}  {_format_number(preferred_peg)}"
    peg_box = "\n".join((
        "┏" + "━" * 46 + "┓",
        f"┃{peg_text:^46}┃",
        "┗" + "━" * 46 + "┛",
    ))
    print("\n" + _style(peg_box, "1;93"))

    print(
        "\n"
        + _style(
            f"盈利预测 · {metrics['base_year']} → {metrics['future_year']}E",
            "1;36",
        )
    )
    print(
        "  归母净利润: "
        f"{_format_yi(metrics['current_net_profit'])} → "
        f"{_format_yi(metrics['future_net_profit'])}"
    )
    print(f"  CAGR: {_format_number(metrics['cagr_percent'], suffix='%')}")
    print(f"  增长倍数: {_format_number(metrics['growth_multiple'], suffix=' 倍')}")

    forward_start_profit = metrics.get("forward_start_net_profit")
    if forward_start_profit is not None:
        forward_start_year = metrics.get("forward_start_year")
        print(
            "\n"
            + _style(
                f"前瞻估值 · {forward_start_year}E → {metrics['future_year']}E",
                "1;36",
            )
        )
        print(
            "  预测归母净利润: "
            f"{_format_yi(forward_start_profit)} → "
            f"{_format_yi(metrics['future_net_profit'])}"
        )
        print(
            "  前瞻 CAGR: "
            f"{_format_number(metrics.get('forward_cagr_percent'), suffix='%')}"
        )
        print(
            "  预测年度 PE: "
            f"{_format_number(metrics.get('forward_pe'), suffix=' 倍')}"
        )
        print(f"  前瞻 PEG: {_format_number(metrics.get('forward_peg'))}")
        print(f"  普通 PEG: {_format_number(metrics.get('peg'))}")

    if metrics.get("abnormal_base"):
        print("\n" + _style("基期异常提示", "1;33"))
        print(f"  ⚠ {metrics.get('base_warning') or '普通 PEG 可比性较低'}")
        if metrics.get("forward_peg") is not None:
            print("  已改用前瞻 PEG 作为主指标，普通 PEG 仅保留作参考")

    print("\n" + _style("预测样本质量", "1;36"))
    forecast_count = metrics.get("forecast_count")
    if forecast_count is not None:
        print(f"  有效机构数: {forecast_count} 家")
    report_start = metrics.get("report_start_date")
    report_end = metrics.get("report_end_date")
    window_days = metrics.get("forecast_window_days")
    if report_start and report_end:
        window_label = f"（最近 {window_days} 天窗口）" if window_days else ""
        print(f"  报告范围: {report_start} → {report_end}{window_label}")
    future_q1 = metrics.get("future_net_profit_q1")
    future_q3 = metrics.get("future_net_profit_q3")
    if future_q1 is not None or future_q3 is not None:
        print(
            "  预测中间区间(Q1–Q3): "
            f"{_format_yi(future_q1)} → {_format_yi(future_q3)}"
        )
    print(f"  机构预测标准差: {_format_yi(metrics['future_net_profit_std'])}")
    divergence = metrics.get("divergence")
    divergence_percent = None if divergence is None else divergence * 100
    print(f"  预测分歧度: {_format_number(divergence_percent, suffix='%')}")

    print("\n" + _style("风险与股东回报", "1;36"))
    print(f"  历史波动率: {volatility or 'N/A'}")
    return_metrics = shareholder_metrics or cash_quality
    has_separated_returns = bool(
        return_metrics and "cash_dividend_per_share_ttm" in return_metrics
    )
    if has_separated_returns:
        print("\n" + _style("股东回报 · 最近 365 天", "1;36"))
        print(
            "  统计窗口: "
            f"{return_metrics.get('window_start_date')} → "
            f"{return_metrics.get('window_end_date')}"
        )
        print(
            "  每股现金分红: "
            f"{_format_number(return_metrics.get('cash_dividend_per_share_ttm'), suffix=' 元')} "
            f"（{return_metrics.get('dividend_event_count', 0)} 次已实施）"
        )
        print(
            "  现金股息率: "
            f"{_format_number(return_metrics.get('cash_dividend_yield_percent'), suffix='%')}"
        )
        print(
            "  已完成注销回购: "
            f"{_format_yi(return_metrics.get('cancelled_buyback_amount_ttm'))} "
            f"（{return_metrics.get('cancelled_buyback_count', 0)} 项）"
        )
        print(
            "  原始回购收益率: "
            f"{_format_number(return_metrics.get('raw_buyback_yield_percent'), suffix='%')}"
        )
        print(
            "  净股本减少率: "
            f"{_format_number(return_metrics.get('net_share_reduction_percent'), suffix='%')}"
        )
        print(
            "  有效回购收益率: "
            f"{_format_number(return_metrics.get('effective_buyback_yield_percent'), suffix='%')}"
        )
        print(
            "  计入估值的股东收益率: "
            f"{_format_number(return_metrics.get('shareholder_return_yield_percent'), suffix='%')}"
        )
        print(f"  调整说明: {return_metrics.get('buyback_adjustment_note') or 'N/A'}")
    else:
        print(
            "  近三年现金分红回购/净利比率: "
            f"{_format_number(dividend_ratio, suffix='%')}"
        )

    if cash_quality:
        print(
            "\n"
            + _style(
                "现金回报质量 · "
                f"{cash_quality['period_start_year']}–{cash_quality['period_end_year']}",
                "1;36",
            )
        )
        if cash_quality.get("unsupported_reason"):
            print(f"  {cash_quality['unsupported_reason']}，修正 PEG 暂不计算")
            print(_style(divider, "1;36"))
            return
        print(
            "  经营现金流 / 资本开支 / 简化自由现金流: "
            f"{_format_yi(cash_quality.get('operating_cash_flow_total'))} / "
            f"{_format_yi(cash_quality.get('capital_expenditure_total'))} / "
            f"{_format_yi(cash_quality.get('free_cash_flow_total'))}"
        )
        cash_conversion = cash_quality.get("cash_conversion")
        coverage = cash_quality.get("shareholder_return_coverage")
        print(
            "  利润现金兑现率: "
            f"{_format_number(None if cash_conversion is None else cash_conversion * 100, suffix='%')}"
        )
        print(
            "  股东回报覆盖率: "
            f"{_format_number(None if coverage is None else coverage * 100, suffix='%')}"
        )
        net_debt = cash_quality.get("net_debt")
        debt_label = "净现金" if net_debt is not None and net_debt < 0 else "净负债"
        debt_value = None if net_debt is None else abs(net_debt)
        print(f"  有息负债: {_format_yi(cash_quality.get('interest_bearing_debt'))}")
        print(f"  {debt_label}: {_format_yi(debt_value)}")
        if has_separated_returns:
            print(
                "  三年平均自由现金流收益率: "
                f"{_format_number(cash_quality.get('free_cash_flow_yield_percent'), suffix='%')}"
            )

        adjusted_basis = metrics.get("adjusted_peg_basis")
        basis_suffix = f" · {adjusted_basis}" if adjusted_basis else ""
        print("\n" + _style(f"PEG 口径对照{basis_suffix}", "1;36"))
        print(f"  普通 PEG: {_format_number(metrics.get('peg'))}")
        if has_separated_returns:
            print(
                "  股东回报修正 PEG: "
                f"{_format_number(cash_quality.get('shareholder_adjusted_peg'))} "
                "（现金股息 + 稀释调整后的回购）"
            )
        else:
            print(
                "  分红回购修正 PEG: "
                f"{_format_number(cash_quality.get('payout_adjusted_peg'))} "
                f"（回报收益率 {_format_number(cash_quality.get('payout_yield_percent'), suffix='%')}）"
            )
        print(
            "  现金约束修正 PEG: "
            f"{_format_number(cash_quality.get('cash_adjusted_peg'))} "
            "（股东收益率与自由现金流收益率取较低者；"
            f"收益率 {_format_number(cash_quality.get('cash_supported_yield_percent'), suffix='%')}）"
        )
    else:
        print("\n" + _style("现金回报质量", "1;36"))
        print("  数据不足，修正 PEG 暂不可用")
    print(_style(divider, "1;36"))


def prompt_stock_code(input_func=input, output_func=print):
    """读取一个有效股票代码；输入 q/quit/exit 时返回 None。"""
    while True:
        stock_code = input_func("请输入股票代码（例如 600519，输入 q 退出）: ").strip()
        if stock_code.lower() in {"q", "quit", "exit"}:
            return None
        if stock_code.isdigit() and len(stock_code) == 6:
            return stock_code
        output_func("请输入有效的6位数字股票代码，或输入 q 退出。")


def analyze_stock(stock_code):
    """抓取并展示一只股票的分析结果；成功返回 True。"""
    stock_suffix = determine_suffix(stock_code)
    print(f"股票代码 {stock_code} 已识别为 {stock_suffix} 市场。")

    print("正在获取实时行情...")
    stock_name, latest_price, pe_ttm, market_cap = get_stock_price_name_pe(stock_code)
    if stock_name:
        print(f"已获取到 {stock_name} 的实时数据。")
    else:
        print("实时行情不完整，将以 N/A 展示缺失指标。")
        stock_name = "未知股票"

    print("正在获取盈利预测数据...")
    forecasts = fetch_profit_forecast(stock_code, stock_suffix)
    if forecasts is None or forecasts.empty:
        print("盈利预测数据抓取失败或为空。")
        return False

    try:
        metrics = calculate_analysis(
            forecasts,
            pe_ttm=pe_ttm,
            market_cap=market_cap,
        )
    except ValueError as error:
        print(f"无法完成盈利预测分析: {error}")
        return False

    volatility = get_historical_volatility(stock_code, stock_suffix)
    dividend_rows, repurchase_rows, equity_rows = fetch_shareholder_return_data(
        stock_code,
        stock_suffix,
    )
    adjusted_pe, adjusted_growth, adjusted_basis = select_adjusted_peg_inputs(
        metrics,
        pe_ttm,
    )
    metrics["adjusted_peg_basis"] = adjusted_basis
    shareholder_metrics = calculate_shareholder_return_metrics(
        dividend_rows=dividend_rows,
        repurchase_rows=repurchase_rows,
        equity_rows=equity_rows,
        latest_price=latest_price,
        market_cap=market_cap,
        pe_ttm=adjusted_pe,
        cagr_percent=adjusted_growth,
        growth_basis="net_profit",
    )
    cash_flow_rows, balance_row = fetch_cash_return_statements(stock_code, stock_suffix)
    cash_quality = calculate_cash_return_quality(
        cash_flow_rows,
        balance_row,
        shareholder_metrics,
        market_cap=market_cap,
        pe_ttm=adjusted_pe,
        cagr_percent=adjusted_growth,
    )
    print_analysis_summary(
        stock_code=stock_code,
        stock_name=stock_name,
        latest_price=latest_price,
        pe_ttm=pe_ttm,
        metrics=metrics,
        volatility=volatility,
        dividend_ratio=None,
        cash_quality=cash_quality,
        shareholder_metrics=shareholder_metrics,
    )
    return True


def main():
    print("A股盈利预测分析器（输入 q 可退出）")
    try:
        while True:
            stock_code = prompt_stock_code()
            if stock_code is None:
                print("已退出。")
                return
            try:
                analyze_stock(stock_code)
            except ValueError as error:
                print(error)
    except (EOFError, KeyboardInterrupt):
        print("\n已退出。")


if __name__ == "__main__":
    main()
