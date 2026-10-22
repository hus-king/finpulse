"""批量跑沪深300成分股，并输出表格。

复用 analyzer.py 的全部抓取与计算函数，只把「打印摘要」换成「收集成行」，
以便一次性导出 CSV / Excel / Markdown。

用法:
    python3.14 run_csi300.py                     # 全量 300 只
    python3.14 run_csi300.py --limit 8           # 先小样本试跑
    python3.14 run_csi300.py --workers 6 --resume
"""

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyzer  # noqa: E402
from selection import GROUP_ORDER, LEGACY_COLUMNS, STATUS_ORDER, assess_csi300_frame, ensure_unarchived  # noqa: E402
from snapshot_diff import (  # noqa: E402
    _attribute_change, _clean_code, _load_any_frame, _load_records,
    compute_diff, format_diff_markdown, run_diff,
)

CSI300_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CSI300_BOARD_URL = "https://push2.eastmoney.com/api/qt/clist/get"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

YI = 1e8


# --------------------------------------------------------------------------
# 并发安全的 stdout 捕获：每只股票的输出进自己的缓冲区，失败时用来提取原因
# --------------------------------------------------------------------------
class _ThreadLocalStdout:
    def __init__(self):
        self._local = threading.local()
        self._real = sys.__stdout__

    def buffer(self):
        buf = getattr(self._local, "buf", None)
        if buf is None:
            buf = io.StringIO()
            self._local.buf = buf
        return buf

    def reset(self):
        self.buffer().seek(0)
        self.buffer().truncate(0)

    def text(self):
        return self.buffer().getvalue()

    def write(self, data):
        try:
            return self.buffer().write(data)
        except Exception:
            return len(data)

    def flush(self):
        pass


_ROUTER = _ThreadLocalStdout()


# --------------------------------------------------------------------------
# 成分股列表
# --------------------------------------------------------------------------
def _clean_name(value):
    return re.sub(r"\s+", "", str(value or ""))


def _dedupe(rows):
    seen = set()
    result = []
    for item in rows:
        code = str(item.get("code") or "").strip()
        if len(code) == 6 and code.isdigit() and code not in seen:
            seen.add(code)
            result.append(item)
    return result


def _fetch_from_datacenter(timeout=20, retries=3):
    """指数成分接口（TYPE=1 为沪深300），额外带行业与权重。"""
    last_error = None
    for attempt in range(retries):
        try:
            collected = []
            page = 1
            while True:
                params = {
                    "reportName": "RPT_INDEX_TS_COMPONENT",
                    "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,INDUSTRY,WEIGHT",
                    "filter": '(TYPE="1")',
                    "pageNumber": page,
                    "pageSize": 500,
                    "source": "WEB",
                    "client": "WEB",
                }
                response = requests.get(
                    CSI300_URL, params=params, headers=HEADERS, timeout=timeout
                )
                response.raise_for_status()
                result = (response.json() or {}).get("result") or {}
                data = result.get("data") or []
                collected.extend(
                    {
                        "code": str(item.get("SECURITY_CODE") or "").strip(),
                        "name": _clean_name(item.get("SECURITY_NAME_ABBR")),
                        "industry": _clean_name(item.get("INDUSTRY")) or None,
                        "weight": analyzer._to_float(item.get("WEIGHT")),
                    }
                    for item in data
                )
                pages = int(result.get("pages") or 0)
                if not data or page >= pages:
                    break
                page += 1
            rows = _dedupe(collected)
            if rows:
                return rows
            last_error = "成分接口未返回数据"
        except Exception as error:  # noqa: BLE001
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(1 + attempt)
    raise RuntimeError(f"datacenter 成分接口失败: {last_error}")


def _fetch_from_board(timeout=15, retries=3):
    """备用：东方财富沪深300板块（BK0500），小分页逐页取。"""
    last_error = None
    for attempt in range(retries):
        try:
            collected = []
            page = 1
            while True:
                params = {
                    "pn": page,
                    "pz": 100,
                    "po": 0,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": "b:BK0500",
                    "fields": "f12,f14",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                }
                response = requests.get(
                    CSI300_BOARD_URL, params=params, headers=HEADERS, timeout=timeout
                )
                response.raise_for_status()
                data = (response.json() or {}).get("data") or {}
                diff = data.get("diff") or []
                collected.extend(
                    {
                        "code": str(item.get("f12") or "").strip(),
                        "name": _clean_name(item.get("f14")),
                    }
                    for item in diff
                )
                if len(collected) >= int(data.get("total") or 0) or not diff:
                    break
                page += 1
            rows = _dedupe(collected)
            if rows:
                return rows
            last_error = "板块接口未返回成分股"
        except Exception as error:  # noqa: BLE001
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(1 + attempt)
    raise RuntimeError(f"板块接口失败: {last_error}")


def fetch_csi300_constituents():
    """获取沪深300全部成分股，主力接口失败时自动换备用接口。"""
    errors = []
    for fetcher in (_fetch_from_datacenter, _fetch_from_board):
        try:
            rows = fetcher()
            if rows:
                return rows
        except Exception as error:  # noqa: BLE001
            errors.append(str(error))
    raise RuntimeError("获取沪深300成分股失败: " + " | ".join(errors))


# --------------------------------------------------------------------------
# 单只股票
# --------------------------------------------------------------------------
def _yi(value):
    number = analyzer._to_float(value)
    return None if number is None else number / YI


def _round(value, digits=2):
    number = analyzer._to_float(value)
    return None if number is None else round(number, digits)


def _parse_percent(text):
    """把 '22.28%' 这类字符串转成 22.28。"""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return _round(text)
    match = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(match.group()) if match else None


def _fail(record, reason, elapsed):
    record["状态"] = "失败"
    record["失败原因"] = reason
    record["耗时_秒"] = round(elapsed, 2)
    return record


def analyze_one(code, name, industry=None, weight=None, window_days=None):
    """复刻 analyzer.analyze_stock 的取数与计算，结果收成一行。"""
    started = time.time()
    record = {
        "代码": code,
        "名称": name,
        "行业": industry,
        "沪深300权重_%": _round(weight, 3),
        "状态": "成功",
    }
    try:
        suffix = analyzer.determine_suffix(code)
        record["市场"] = suffix

        stock_name, price, pe_ttm, market_cap = analyzer.get_stock_price_name_pe(code)
        if stock_name:
            record["名称"] = stock_name
        record["最新价"] = _round(price)
        record["PE_TTM"] = _round(pe_ttm)
        record["总市值_亿"] = _round(_yi(market_cap))

        if price is None or market_cap is None or market_cap <= 0:
            return _fail(record, "实时行情获取失败（价格/市值缺失）", time.time() - started)

        forecast_kwargs = {}
        if window_days is not None:
            forecast_kwargs["window_days"] = window_days
        forecasts = analyzer.fetch_profit_forecast(code, suffix, **forecast_kwargs)
        if forecasts is None or forecasts.empty:
            return _fail(record, "无可用盈利预测数据", time.time() - started)

        try:
            metrics = analyzer.calculate_analysis(
                forecasts, pe_ttm=pe_ttm, market_cap=market_cap
            )
        except ValueError as error:
            return _fail(record, f"盈利预测不可计算: {error}", time.time() - started)

        volatility = analyzer.get_historical_volatility(code, suffix)
        dividend_rows, repurchase_rows, equity_rows = (
            analyzer.fetch_shareholder_return_data(code, suffix)
        )
        adjusted_pe, adjusted_growth, adjusted_basis = analyzer.select_adjusted_peg_inputs(
            metrics, pe_ttm
        )
        metrics["adjusted_peg_basis"] = adjusted_basis
        shareholder = analyzer.calculate_shareholder_return_metrics(
            dividend_rows=dividend_rows,
            repurchase_rows=repurchase_rows,
            equity_rows=equity_rows,
            latest_price=price,
            market_cap=market_cap,
            pe_ttm=adjusted_pe,
            cagr_percent=adjusted_growth,
            growth_basis="net_profit",
        )
        cash_flow_rows, balance_row = analyzer.fetch_cash_return_statements(code, suffix)
        cash = analyzer.calculate_cash_return_quality(
            cash_flow_rows,
            balance_row,
            shareholder,
            market_cap=market_cap,
            pe_ttm=adjusted_pe,
            cagr_percent=adjusted_growth,
        )

        # ---- 盈利预测 ----
        record.update(
            {
                "基期年": metrics.get("base_year"),
                "基期净利润_亿": _round(_yi(metrics.get("current_net_profit"))),
                "预测年": metrics.get("future_year"),
                "预测净利润_亿": _round(_yi(metrics.get("future_net_profit"))),
                "增长倍数": _round(metrics.get("growth_multiple"), 3),
                "CAGR_%": _round(metrics.get("cagr_percent")),
                "机构数": metrics.get("forecast_count"),
                "报告起始": metrics.get("report_start_date"),
                "报告截止": metrics.get("report_end_date"),
                "预测Q1_亿": _round(_yi(metrics.get("future_net_profit_q1"))),
                "预测Q3_亿": _round(_yi(metrics.get("future_net_profit_q3"))),
                "预测标准差_亿": _round(_yi(metrics.get("future_net_profit_std"))),
                "预测分歧度_%": _round(
                    None
                    if metrics.get("divergence") is None
                    else metrics["divergence"] * 100
                ),
                "普通PEG": _round(metrics.get("peg")),
                "前瞻起始年": metrics.get("forward_start_year"),
                "前瞻起始净利润_亿": _round(_yi(metrics.get("forward_start_net_profit"))),
                "前瞻CAGR_%": _round(metrics.get("forward_cagr_percent")),
                "前瞻PE": _round(metrics.get("forward_pe")),
                "前瞻PEG": _round(metrics.get("forward_peg")),
                "采用PEG": _round(metrics.get("preferred_peg")),
                "采用口径": metrics.get("preferred_peg_label"),
                "基期异常": "是" if metrics.get("abnormal_base") else "否",
                "基期异常说明": metrics.get("base_warning"),
                "PEG计算口径": adjusted_basis,
            }
        )

        # ---- 风险与股东回报 ----
        record.update(
            {
                "历史波动率_%": _parse_percent(volatility),
                "每股现金分红_元": _round(shareholder.get("cash_dividend_per_share_ttm")),
                "股息率_%": _round(shareholder.get("cash_dividend_yield_percent")),
                "分红次数": shareholder.get("dividend_event_count"),
                "注销回购_亿": _round(_yi(shareholder.get("cancelled_buyback_amount_ttm"))),
                "原始回购收益率_%": _round(shareholder.get("raw_buyback_yield_percent")),
                "净股本减少_%": _round(shareholder.get("net_share_reduction_percent")),
                "有效回购收益率_%": _round(
                    shareholder.get("effective_buyback_yield_percent")
                ),
                "股东收益率_%": _round(shareholder.get("shareholder_return_yield_percent")),
                "股东回报修正PEG": _round(shareholder.get("shareholder_adjusted_peg")),
                "回购调整说明": shareholder.get("buyback_adjustment_note"),
            }
        )

        # ---- 现金回报质量 ----
        if cash:
            record.update(
                {
                    "现金流期间": f"{cash.get('period_start_year')}-{cash.get('period_end_year')}",
                    "经营现金流_亿": _round(_yi(cash.get("operating_cash_flow_total"))),
                    "资本开支_亿": _round(_yi(cash.get("capital_expenditure_total"))),
                    "自由现金流_亿": _round(_yi(cash.get("free_cash_flow_total"))),
                    "利润现金兑现率": _round(cash.get("cash_conversion")),
                    "股东回报覆盖率": _round(cash.get("shareholder_return_coverage")),
                    "有息负债_亿": _round(_yi(cash.get("interest_bearing_debt"))),
                    "净负债_亿": _round(_yi(cash.get("net_debt"))) if cash.get("net_debt") is not None else None,
                    "平均FCF收益率_%": _round(cash.get("free_cash_flow_yield_percent")),
                    "现金支持收益率_%": _round(cash.get("cash_supported_yield_percent")),
                    "现金约束修正PEG": _round(cash.get("cash_adjusted_peg")),
                    "现金口径限制": cash.get("unsupported_reason"),
                }
            )
        else:
            record["现金口径限制"] = "现金流量表数据不足，未计算"

        record["耗时_秒"] = round(time.time() - started, 2)
        return record

    except ValueError as error:
        return _fail(record, f"参数/数据错误: {error}", time.time() - started)
    except Exception as error:  # noqa: BLE001
        return _fail(record, f"{type(error).__name__}: {error}", time.time() - started)


def analyze_with_retry(item, retries=2, window_days=None):
    """网络抖动时重试；单只股票最多尝试 retries 次。"""
    code, name = item["code"], item["name"]
    industry, weight = item.get("industry"), item.get("weight")
    retries = max(1, retries)
    last = None
    for attempt in range(retries):
        _ROUTER.reset()
        try:
            record = analyze_one(
                code, name, industry=industry, weight=weight, window_days=window_days
            )
        except Exception as error:  # noqa: BLE001
            record = _fail(
                {"代码": code, "名称": name, "行业": industry, "沪深300权重_%": _round(weight, 3)},
                f"{type(error).__name__}: {error}",
                0,
            )
        if record.get("状态") == "成功":
            return record, _ROUTER.text()
        last = (record, _ROUTER.text())
        if attempt + 1 < retries:
            time.sleep(1.5 * (attempt + 1))
    return last


# --------------------------------------------------------------------------
# 表格输出
# --------------------------------------------------------------------------
SUMMARY_COLUMNS = [
    ("代码", "代码"),
    ("名称", "名称"),
    ("最新价", "最新价"),
    ("前瞻PEG", "前瞻PEG"),
    ("组内PEG排名", "组内PEG排名"),
    ("比较组", "比较组"),
    ("筛选状态", "筛选状态"),
    ("PE_TTM", "PE(TTM)"),
    ("前瞻PE", "前瞻PE"),
    ("前瞻CAGR_%", "前瞻CAGR%"),
    ("行业", "行业"),
    ("可信度等级", "可信度"),
    ("总市值_亿", "总市值(亿)"),
    ("压力PEG", "压力PEG"),
    ("预测分歧度_%", "分歧度%"),
    ("股息率_%", "股息率%"),
    ("股东收益率_%", "股东收益率%"),
    ("历史波动率_%", "波动率%"),
    ("机构数", "机构数"),
    ("风险标签", "风险标签"),
]


def _sort_key(frame, sort_column):
    if sort_column in LEGACY_COLUMNS:
        raise ValueError("旧综合评分和评级已停用，请使用前瞻PEG")
    if sort_column not in frame.columns:
        raise ValueError(f"排序字段不存在: {sort_column}")
    frame = frame.copy()
    if "筛选状态" not in frame or "比较组" not in frame:
        frame = assess_csi300_frame(frame)
    frame["_status"] = frame["筛选状态"].map(STATUS_ORDER).fillna(len(STATUS_ORDER))
    frame["_group"] = frame["比较组"].map(GROUP_ORDER).fillna(len(GROUP_ORDER))
    numeric_series = pd.to_numeric(frame[sort_column], errors="coerce")
    non_null_count = frame[sort_column].notna().sum()
    if non_null_count > 0 and numeric_series.notna().sum() >= non_null_count * 0.5:
        frame["_sort"] = numeric_series
    else:
        frame["_sort"] = frame[sort_column].fillna("").astype(str)
    descending_columns = {"数据可信度分"}
    frame = frame.sort_values(
        ["_status", "_group", "_sort", "代码"],
        ascending=[True, True, sort_column not in descending_columns, True],
        na_position="last",
        kind="stable",
    )
    return frame.drop(columns=["_status", "_group", "_sort"])


def _num(value):
    number = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(number) or not math.isfinite(float(number)) else float(number)


def derive_notes(frame):
    """给每行生成一句备注：说明 PEG 缺失、亏损、机构覆盖不足等原因。"""
    notes = []
    for _, row in frame.iterrows():
        parts = []
        ok = row.get("状态") == "成功"
        if not ok:
            parts.append(str(row.get("失败原因") or "分析失败"))
        else:
            peg = _num(row.get("采用PEG"))
            pe = _num(row.get("PE_TTM"))
            cagr = _num(row.get("CAGR_%"))
            pe_negative = pe is not None and pe < 0
            if peg is None:
                if pe_negative:
                    parts.append("当前TTM亏损，PE为负，PEG不适用")
                elif pe is None:
                    parts.append("PE(TTM)缺失，PEG不可计算")
                elif cagr is None or cagr <= 0:
                    parts.append("预测净利润增速非正，PEG不适用")
                else:
                    parts.append("前瞻数据不足，PEG不可用")
            elif pe_negative:
                parts.append("当前TTM亏损")
            count = _num(row.get("机构数"))
            if count is not None and count <= 2:
                parts.append(f"机构覆盖不足({int(count)}家)")
            if str(row.get("基期异常")) == "是":
                basis = str(row.get("采用口径") or "")
                parts.append(
                    "基期异常，改用前瞻PEG" if "前瞻" in basis else "基期异常，PEG可比性低"
                )
            limit = row.get("现金口径限制")
            if isinstance(limit, str) and limit.strip():
                parts.append(limit.strip())
        notes.append("；".join(parts) if parts else "")
    return notes


def _is_record_complete(item):
    """判断单只股票的数据是否抓取完整且成功（必须包含有效状态和核心行情指标）。"""
    if not isinstance(item, dict):
        return False
    if item.get("状态") != "成功":
        return False
    price = _num(item.get("最新价"))
    cap = _num(item.get("总市值_亿"))
    if price is None or price <= 0 or cap is None or cap <= 0:
        return False
    return True


def write_outputs(frame, prefix, sort_column="前瞻PEG"):
    ensure_unarchived([prefix + suffix for suffix in (".csv", ".xlsx", ".md")])
    os.makedirs(os.path.dirname(prefix) or ".", exist_ok=True)
    if not frame.empty and "代码" in frame.columns:
        frame["代码"] = frame["代码"].apply(_clean_code)
        frame = frame.drop_duplicates(subset=["代码"], keep="last")
    frame = assess_csi300_frame(frame)
    frame["备注"] = derive_notes(frame)
    ordered = _sort_key(frame, sort_column)
    front_columns = [
        "代码", "名称", "最新价", "前瞻PEG", "组内PEG排名", "比较组", "筛选状态",
        "前瞻PE", "前瞻CAGR_%", "PE_TTM", "可信度等级",
    ]
    columns = [column for column in front_columns if column in ordered.columns]
    ordered = ordered[columns + [column for column in ordered.columns if column not in columns]]
    csv_path = f"{prefix}.csv"
    xlsx_path = f"{prefix}.xlsx"
    md_path = f"{prefix}.md"

    ordered.to_csv(csv_path, index=False, encoding="utf-8-sig")

    try:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            ordered.to_excel(writer, index=False, sheet_name="沪深300")
            sheet = writer.sheets["沪深300"]
            sheet.freeze_panes = "C2"
            for index, column in enumerate(ordered.columns, start=1):
                width = max(10, min(22, int(ordered[column].astype(str).str.len().max() if len(ordered) else 10) + 2))
                sheet.column_dimensions[
                    sheet.cell(row=1, column=index).column_letter
                ].width = width
    except Exception as error:  # noqa: BLE001
        print(f"Excel 写入失败（CSV 不受影响）: {error}")
        xlsx_path = None

    rows = []
    for key, label in SUMMARY_COLUMNS:
        if key in ordered.columns:
            rows.append((label, ordered[key]))
    if rows:
        md = pd.DataFrame({label: series for label, series in rows})
        lines = [
            "| " + " | ".join(md.columns) + " |",
            "|" + "|".join(["---"] * len(md.columns)) + "|",
        ]
        for _, row in md.iterrows():
            cells = []
            for value in row:
                if value is None or pd.isna(value):
                    cells.append("")
                else:
                    cells.append(str(value))
            lines.append("| " + " | ".join(cells) + " |")
        with open(md_path, "w", encoding="utf-8") as handle:
            direction = "降序" if sort_column == "数据可信度分" else "升序"
            handle.write(f"# 沪深300 分析结果（合格优先，金融/非金融分组，组内按{sort_column}{direction}）\n\n")
            handle.write("\n".join(lines) + "\n")

    return csv_path, xlsx_path, md_path


def main():
    parser = argparse.ArgumentParser(description="批量分析沪深300并输出表格")
    parser.add_argument("--workers", type=int, default=6, help="并发线程数")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 只（试跑用）")
    parser.add_argument("--resume", action="store_true", help="跳过 jsonl 里已完成且数据完整的股票（要求状态成功且行情完整）")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="保留已有完整成功的股票，仅重跑失败或数据残缺的股票",
    )
    parser.add_argument(
        "--retry-incomplete", "--retry-unscored", dest="retry_unscored",
        action="store_true",
        help="按筛选状态补关键数据缺失或采集失败项，旧选项--retry-unscored为兼容别名",
    )
    parser.add_argument("--prefix", default="output/csi300", help="输出文件前缀")
    parser.add_argument("--output-prefix", default=None, help="仅重建时使用：输入仍由--prefix定位，结果另存新前缀")
    parser.add_argument("--sort", default="前瞻PEG", help="组内排序字段，默认有效前瞻PEG升序")
    parser.add_argument("--window-days", type=int, default=None, help="覆盖预测报告时间窗口")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="不联网，仅用已有 jsonl 重新生成表格",
    )
    parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("OLD_FILE", "NEW_FILE"),
        help="对比两次运行结果（支持 .jsonl / .xlsx / .csv）",
    )
    parser.add_argument(
        "--diff-output",
        default=None,
        help="跨期异动对比报告导出路径（如 outputs/diff_report.md）",
    )
    parser.add_argument(
        "--min-profit-pct",
        type=float,
        default=1.0,
        help="预测利润显著变动百分比门槛（默认 1.0%%）",
    )
    parser.add_argument(
        "--min-peg-delta",
        type=float,
        default=0.1,
        help="前瞻PEG显著变动绝对值门槛（默认 0.1）",
    )
    args = parser.parse_args()

    if args.diff:
        run_diff(
            args.diff[0],
            args.diff[1],
            output_path=args.diff_output,
            min_profit_pct=args.min_profit_pct,
            min_peg_delta=args.min_peg_delta,
        )
        return

    if args.rebuild:
        rebuild(args)
        return
    if args.output_prefix:
        parser.error("--output-prefix仅用于--rebuild")
    ensure_unarchived([args.prefix + suffix for suffix in ("_raw.jsonl", ".log", ".csv", ".xlsx", ".md")])

    if args.window_days:
        analyzer.DEFAULT_FORECAST_WINDOW_DAYS = args.window_days

    print("正在获取沪深300成分股...", flush=True)
    constituents = fetch_csi300_constituents()
    print(f"沪深300成分股: {len(constituents)} 只", flush=True)

    jsonl_path = f"{args.prefix}_raw.jsonl"
    os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)

    existing_map = {}
    if (args.resume or args.retry_failed or args.retry_unscored) and os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                code = _clean_code(item.get("代码"))
                if code:
                    existing_map[code] = item

    done = {}
    if args.retry_unscored and existing_map:
        assessed_existing = assess_csi300_frame(pd.DataFrame(list(existing_map.values())))
        for _, row in assessed_existing.iterrows():
            code = _clean_code(row.get("代码"))
            valid_quote = (_num(row.get("最新价")) is not None and _num(row.get("最新价")) > 0
                           and _num(row.get("总市值_亿")) is not None and _num(row.get("总市值_亿")) > 0)
            if row.get("筛选状态") in ("合格", "观察", "不适用") and valid_quote:
                done[code] = existing_map[code]
        print(
            f"按筛选状态补采: 已有 {len(done)} 只完整记录，跳过；待补跑 {len(constituents) - len(done)} 只",
            flush=True,
        )
    elif (args.resume or args.retry_failed) and existing_map:
        for code, item in existing_map.items():
            if _is_record_complete(item):
                done[code] = item
        mode_label = "断点续跑" if args.resume else "重跑失败标的"
        print(
            f"{mode_label}: 已有 {len(done)} 只完整成功结果，跳过；待重跑/补跑 {len(constituents) - len(done)} 只",
            flush=True,
        )

    todo = [item for item in constituents if item["code"] not in done]
    if args.limit:
        todo = todo[: args.limit]

    records = list(done.values())
    write_lock = threading.Lock()
    log_handle = open(jsonl_path, "a", encoding="utf-8")
    started = time.time()
    finished = 0
    # 抓取过程会把 stdout 重定向到线程缓冲，进度必须显式写到真实终端
    console = sys.stdout

    print(f"开始分析 {len(todo)} 只，并发 {args.workers} ...", flush=True)
    with contextlib.redirect_stdout(_ROUTER):
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(analyze_with_retry, item, window_days=args.window_days): item
                for item in todo
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    record, logs = future.result()
                except Exception as error:  # noqa: BLE001
                    record = _fail(
                        {"代码": item["code"], "名称": item["name"], "行业": item.get("industry")},
                        f"{type(error).__name__}: {error}",
                        0,
                    )
                    logs = ""
                record["_日志"] = logs.strip()[-2000:]
                with write_lock:
                    records.append(record)
                    log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log_handle.flush()
                    finished += 1
                    flag = "OK " if record.get("状态") == "成功" else "FAIL"
                    print(
                        f"[{finished}/{len(todo)}] {flag} {record['代码']} "
                        f"{record.get('名称', '')} "
                        f"{record.get('采用PEG', '')} "
                        f"{record.get('失败原因', '')}",
                        file=console,
                        flush=True,
                    )
    log_handle.close()

    frame = pd.DataFrame(records)
    if frame.empty:
        print("没有任何结果。")
        return
    frame = frame.drop(columns=["_日志"], errors="ignore")
    if not frame.empty and "代码" in frame.columns:
        frame["代码"] = frame["代码"].apply(_clean_code)
        frame = frame.drop_duplicates(subset=["代码"], keep="last")
    csv_path, xlsx_path, md_path = write_outputs(frame, args.prefix, args.sort)
    _report(frame, started, jsonl_path, csv_path, xlsx_path, md_path)


def _report(frame, started, jsonl_path, csv_path, xlsx_path, md_path):
    if not frame.empty and "代码" in frame:
        frame = frame.drop_duplicates("代码", keep="last")
    assessed = assess_csi300_frame(frame)
    counts = assessed["筛选状态"].value_counts()
    print("\n" + "=" * 60)
    print(f"【沪深300 前瞻PEG筛选统计】 (总计 {len(frame)} 只)")
    for status in STATUS_ORDER:
        print(f"  {status}: {int(counts.get(status, 0))} 只")
    print("  排序: 合格优先，金融/非金融分开，各组有效前瞻PEG由低到高")
    print("  风险和数据可信度独立展示；观察、不适用或失败行不参与组内排名")
    print(f"耗时 {time.time() - started:.0f} 秒")
    print(f"CSV   : {csv_path}")
    if xlsx_path:
        print(f"Excel : {xlsx_path}")
    print(f"表格  : {md_path}")
    print(f"原始  : {jsonl_path}")
    print("=" * 60)


def rebuild(args):
    """不联网，仅用已有 jsonl 重新生成表格。"""
    jsonl_path = f"{args.prefix}_raw.jsonl"
    if not os.path.exists(jsonl_path):
        raise SystemExit(f"找不到原始记录: {jsonl_path}")
    started = time.time()
    frame = pd.DataFrame(_load_records(jsonl_path))
    if frame.empty:
        raise SystemExit("原始记录为空。")
    prefix = getattr(args, "output_prefix", None) or args.prefix
    csv_path, xlsx_path, md_path = write_outputs(frame, prefix, args.sort)
    _report(frame, started, jsonl_path, csv_path, xlsx_path, md_path)





if __name__ == "__main__":
    main()
