"""日常快照读取与PEG异动；不读取或比较旧加权分和A/B/C/D评级。"""

import json
import os
import re
from pathlib import Path

import pandas as pd

from selection import _clean_code, assess_csi300_frame, ensure_unarchived, number


def _load_records(path):
    records = []
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append({key: value for key, value in item.items() if key != "_日志"})
    frame = pd.DataFrame(records)
    if not frame.empty and "代码" in frame:
        frame["代码"] = frame["代码"].map(_clean_code)
        frame = frame.drop_duplicates("代码", keep="last")
    return frame.to_dict(orient="records")


def _snapshot_date(path):
    for date in reversed(re.findall(r"(?<!\d)(20\d{6})(?!\d)", str(path))):
        try:
            return pd.to_datetime(date, format="%Y%m%d")
        except ValueError:
            continue
    return None


def _load_any_frame(path):
    source = Path(path)
    if not source.exists():
        raise SystemExit(f"文件不存在: {path}")
    extension = source.suffix.lower()
    if extension in (".jsonl", ".ndjson"):
        frame = pd.DataFrame(_load_records(path))
    elif extension in (".xlsx", ".xls"):
        with pd.ExcelFile(path) as workbook:
            sheet = "沪深300" if "沪深300" in workbook.sheet_names else workbook.sheet_names[0]
            for header in (0, 1, 2, 3, 6, 7):
                frame = pd.read_excel(workbook, sheet_name=sheet, header=header)
                if "代码" in frame:
                    break
    elif extension == ".csv":
        try:
            frame = pd.read_csv(path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            frame = pd.read_csv(path, encoding="gbk")
    else:
        raise SystemExit(f"不支持的文件格式: {extension}")
    if frame.empty or "代码" not in frame:
        raise SystemExit(f"文件无有效股票记录或代码列: {path}")
    frame["代码"] = frame["代码"].map(_clean_code)
    frame = frame.drop_duplicates("代码", keep="last")
    as_of = _snapshot_date(path)
    if "评估日期" in frame and frame["评估日期"].notna().any():
        dates = pd.to_datetime(frame["评估日期"], errors="coerce")
        if dates.isna().any() or dates.dt.date.nunique() != 1:
            raise ValueError(f"快照评估日期缺失、无效或不一致: {path}")
        as_of = dates.iloc[0]
    return assess_csi300_frame(frame, as_of_date=as_of)


def _parse_forecast_year(value):
    numeric = number(value)
    if numeric is not None and numeric.is_integer() and 1900 <= numeric <= 2199:
        return int(numeric)
    match = re.search(r"(?<!\d)((?:19|20|21)\d{2})(?!\d)", str(value) if pd.notna(value) else "")
    return int(match.group(1)) if match else None


def _attribute_change(old, new, delta_peg=None):
    status_new = str(new.get("筛选状态") or new.get("状态") or "")
    status_old = str(old.get("筛选状态") or old.get("状态") or "")
    if "失败" in status_new:
        return f"⚠️ 数据采集异常 ({new.get('失败原因') or '网络或接口异常'})"
    if "缺失" in status_new or (number(new.get("最新价")) is None and number(old.get("最新价")) is not None):
        return "⚠️ 数据采集异常 (实时行情或关键输入缺失)"
    if ("失败" in status_old or "缺失" in status_old) and status_new in ("合格", "成功", "观察"):
        return "数据采集恢复"
    drivers = []
    year_old, year_new = _parse_forecast_year(old.get("预测年")), _parse_forecast_year(new.get("预测年"))
    profit_old, profit_new = number(old.get("预测净利润_亿")), number(new.get("预测净利润_亿"))
    if year_old is not None and year_new is not None and year_old != year_new:
        drivers.append("预测期限滚动")
    elif profit_old is not None and profit_old > 0 and profit_new is not None and abs(profit_new / profit_old - 1) >= .01:
        drivers.append("盈利预测变动" if year_old is not None and year_old == year_new else "预测年份缺失，盈利变化不可比")
    price_old, price_new = number(old.get("最新价")), number(new.get("最新价"))
    if price_old is not None and price_old > 0 and price_new is not None and abs(price_new / price_old - 1) >= .02:
        drivers.append("股价行情驱动")
    for columns, threshold, label in [
        (("股东收益率_%",), .3, "股东回报变化"),
        (("机构数", "预测分歧度_%"), 2., "预测可信度变化"),
        (("平均FCF收益率_%",), .5, "现金质量变化"),
        (("利润现金兑现率",), .1, "现金质量变化"),
        (("历史波动率_%",), 2., "风险波动变化"),
    ]:
        if any(number(old.get(column)) is not None and number(new.get(column)) is not None
               and abs(number(new.get(column)) - number(old.get(column))) >= threshold for column in columns):
            drivers.append(label)
    return "；".join(dict.fromkeys(drivers)) or "未识别到显著输入变化"


def compute_diff(df_old, df_new, min_profit_pct=1., min_peg_delta=.1, min_yield_delta=.3):
    if any(number(value) is None or value < 0 for value in (min_profit_pct, min_peg_delta, min_yield_delta)):
        raise ValueError("异动门槛必须为非负有限数")
    old_map = {_clean_code(row["代码"]): row for _, row in df_old.iterrows()}
    new_map = {_clean_code(row["代码"]): row for _, row in df_new.iterrows()}
    common = sorted(set(old_map) & set(new_map))
    result = {key: [] for key in ("status_changes", "profit_upgrades", "profit_downgrades",
              "rolling_horizon_stocks", "uncomparable_forecasts", "peg_improvers", "peg_deteriorations", "yield_changes")}
    result["summary"] = dict(total_old=len(df_old), total_new=len(df_new), common_count=len(common),
                             added_stocks=[{"代码": code, "名称": new_map[code].get("名称", "")} for code in sorted(set(new_map) - set(old_map))],
                             removed_stocks=[{"代码": code, "名称": old_map[code].get("名称", "")} for code in sorted(set(old_map) - set(new_map))])
    for code in common:
        old, new = old_map[code], new_map[code]
        base = {"代码": code, "名称": new.get("名称") or old.get("名称") or code, "行业": new.get("行业") or old.get("行业") or ""}
        status_old, status_new = old.get("筛选状态") or old.get("状态"), new.get("筛选状态") or new.get("状态")
        if pd.notna(status_old) and pd.notna(status_new) and status_old != status_new:
            result["status_changes"].append(dict(base, 原状态=status_old, 新状态=status_new, 变动归因=_attribute_change(old, new)))
        year_old, year_new = _parse_forecast_year(old.get("预测年")), _parse_forecast_year(new.get("预测年"))
        profit_old, profit_new = number(old.get("预测净利润_亿")), number(new.get("预测净利润_亿"))
        growth_old, growth_new = number(old.get("前瞻CAGR_%")), number(new.get("前瞻CAGR_%"))
        growth_delta = growth_new - growth_old if growth_old is not None and growth_new is not None else None
        if year_old is not None and year_new is not None and year_old != year_new:
            result["rolling_horizon_stocks"].append(dict(base, 原预测年=f"{year_old}E", 新预测年=f"{year_new}E",
                原预测利润=profit_old, 新预测利润=profit_new, 原CAGR=growth_old, 新CAGR=growth_new, CAGR变动=growth_delta))
        elif profit_old is not None and profit_old > 0 and profit_new is not None and profit_new > 0:
            if year_old is None or year_new is None:
                result["uncomparable_forecasts"].append(dict(base, 原预测年=f"{year_old}E" if year_old else "未知",
                    新预测年=f"{year_new}E" if year_new else "未知", 原预测利润=profit_old, 新预测利润=profit_new, 不可比原因="预测年份缺失"))
            else:
                percent = (profit_new / profit_old - 1) * 100
                if percent != 0 and abs(percent) >= min_profit_pct:
                    result["profit_upgrades" if percent > 0 else "profit_downgrades"].append(
                        dict(base, 目标年份=f"{year_new}E", 原预测利润=profit_old, 新预测利润=profit_new,
                             利润变动=profit_new - profit_old, **{"幅度_%": percent}, CAGR变动=growth_delta))
        else:
            reasons = ["预测利润缺失、非有限或非正数"]
            if year_old is None or year_new is None:
                reasons.append("预测年份缺失")
            result["uncomparable_forecasts"].append(dict(base, 原预测年=f"{year_old}E" if year_old else "未知",
                新预测年=f"{year_new}E" if year_new else "未知", 原预测利润=profit_old, 新预测利润=profit_new,
                不可比原因="；".join(reasons)))
        peg_old, peg_new = number(old.get("前瞻PEG")), number(new.get("前瞻PEG"))
        if peg_old is not None and peg_old > 0 and peg_new is not None and peg_new > 0 and peg_new != peg_old and abs(peg_new - peg_old) >= min_peg_delta:
            result["peg_improvers" if peg_new < peg_old else "peg_deteriorations"].append(
                dict(base, 原前瞻PEG=peg_old, 新前瞻PEG=peg_new, PEG变动=peg_new - peg_old, 变动归因=_attribute_change(old, new)))
        yield_old, yield_new = number(old.get("股东收益率_%")), number(new.get("股东收益率_%"))
        if yield_old is not None and yield_new is not None and yield_new != yield_old and abs(yield_new - yield_old) >= min_yield_delta:
            result["yield_changes"].append(dict(base, 原收益率=yield_old, 新收益率=yield_new, 收益率变动=yield_new - yield_old))
    for key, column in (("profit_upgrades", "幅度_%"), ("profit_downgrades", "幅度_%"),
                        ("peg_improvers", "PEG变动"), ("peg_deteriorations", "PEG变动"), ("yield_changes", "收益率变动")):
        result[key] = sorted(result[key], key=lambda row: abs(row[column]), reverse=True)[:10]
    result["summary"].update(rolling_horizon_count=len(result["rolling_horizon_stocks"]),
        forecast_year_missing_count=sum("年份缺失" in row["不可比原因"] for row in result["uncomparable_forecasts"]),
        forecast_incomparable_count=len(result["uncomparable_forecasts"]))
    return result


def format_diff_markdown(result, old_label="基期", new_label="最新期"):
    summary = result["summary"]
    lines = ["# 沪深300 跨期异动监测报告", "", f"- 基期数据：{old_label}（{summary['total_old']}只）",
             f"- 最新数据：{new_label}（{summary['total_new']}只）", f"- 共同标的：{summary['common_count']}只",
             "- 口径：前瞻PEG、有效性与独立风险；金融和非金融只在组内比较。",
             "- PEG下降不必然代表基本面改善，可能来自价格变化、预测调整或预测期限滚动。", ""]
    sections = [
        ("筛选状态变化", "status_changes", ["原状态", "新状态", "变动归因"]),
        ("机构盈利预测上调（同年度）", "profit_upgrades", ["目标年份", "原预测利润", "新预测利润", "利润变动", "幅度_%", "CAGR变动"]),
        ("机构盈利预测下修（同年度）", "profit_downgrades", ["目标年份", "原预测利润", "新预测利润", "利润变动", "幅度_%", "CAGR变动"]),
        ("预测基准年滚动：利润不可直接相减", "rolling_horizon_stocks", ["原预测年", "新预测年", "原预测利润", "新预测利润", "CAGR变动"]),
        ("预测年份或利润无效：利润不可比", "uncomparable_forecasts", ["原预测年", "新预测年", "不可比原因"]),
        ("前瞻PEG下降", "peg_improvers", ["原前瞻PEG", "新前瞻PEG", "PEG变动", "变动归因"]),
        ("前瞻PEG上升", "peg_deteriorations", ["原前瞻PEG", "新前瞻PEG", "PEG变动", "变动归因"]),
        ("股东回报变化", "yield_changes", ["原收益率", "新收益率", "收益率变动"]),
    ]
    for title, key, fields in sections:
        lines.extend(["## " + title, ""])
        if not result[key]:
            lines.extend(["暂无显著变动。", ""])
            continue
        columns = ["代码", "名称", "行业"] + fields
        lines.extend(["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"])
        for row in result[key][:15]:
            cells = []
            for column in columns:
                value = row.get(column)
                text = "-" if value is None or pd.isna(value) else f"{value:.2f}" if isinstance(value, float) else str(value)
                cells.append(text.replace("|", "\\|").replace("\n", " "))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    lines.extend(["## 成分股进出名单", ""])
    for key, title in (("added_stocks", "新进"), ("removed_stocks", "剔除")):
        lines.append(f"- {title}：" + ("、".join(row["代码"] + " " + str(row["名称"]) for row in summary[key]) or "无"))
    return "\n".join(lines)


def run_diff(old_path, new_path, output_path=None, min_profit_pct=1., min_peg_delta=.1):
    old, new = _load_any_frame(old_path), _load_any_frame(new_path)
    result = compute_diff(old, new, min_profit_pct=min_profit_pct, min_peg_delta=min_peg_delta)
    report = format_diff_markdown(result, os.path.basename(old_path), os.path.basename(new_path))
    if output_path:
        ensure_unarchived([output_path])
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report, encoding="utf-8")
        print(f"已导出跨期异动报告至: {output_path}")
    print(report)
    return result
