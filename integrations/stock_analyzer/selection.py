"""当前选股：前瞻PEG组内排序，数据有效性、可信度和风险独立展示。"""

import math
import re
from pathlib import Path

import pandas as pd


LEGACY_COLUMNS = (
    "综合评分", "评分等级", "评分状态", "评分模型", "估值分", "压力韧性分",
    "预测可信度分", "现金质量分", "股东回报分", "风险分", "入选理由", "可比范围",
    "可比行业", "行业PEG中位数", "行业PEG排名", "行业PEG分位_%",
    "评级", "等级", "综合分", "总分",
)
ASSESSMENT_COLUMNS = (
    "比较组", "筛选状态", "前瞻PEG", "组内PEG排名", "组内样本数", "数据可信度分",
    "可信度等级", "风险标签", "评估日期", "压力CAGR_%", "压力PEG", "采用PEG", "采用口径",
)
GROUP_ORDER = {"非金融": 0, "金融": 1}
STATUS_ORDER = {"合格": 0, "观察": 1, "不适用": 2, "数据缺失": 3, "采集失败": 4}


def ensure_unarchived(paths):
    root = Path(__file__).resolve().parent
    manifest = root / "outputs/ARCHIVE_SHA256SUMS"
    if not manifest.exists():
        return
    archived = {str(root / line.split("  ", 1)[1]) for line in manifest.read_text(encoding="utf-8").splitlines() if "  " in line}
    for path in paths:
        if str(Path(path).resolve()) in archived:
            raise ValueError(f"拒绝覆盖历史归档: {path}；请使用新的--prefix或重建时指定--output-prefix")


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _clean_code(value):
    if pd.isna(value):
        return ""
    match = re.search(r"\b(\d{6})\b", str(value).strip())
    if match:
        return match.group(1)
    try:
        return f"{int(float(value)):06d}"
    except (TypeError, ValueError, OverflowError):
        return str(value).strip()


def _scale(value, bad, good):
    if value is None:
        return 0.
    return max(0., min(100., (value - bad) / (good - bad) * 100))


def _financial(row):
    industry = " ".join(str(row.get(column)) for column in ("行业", "历史一级行业") if pd.notna(row.get(column)))
    return any(keyword in industry for keyword in ("银行", "保险", "证券", "金融", "城商行", "农商行"))


def _report_age(value, as_of):
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else (as_of - parsed.date()).days


def _forecast_valid(row):
    for column in ("前瞻PEG", "前瞻PE", "前瞻CAGR_%", "前瞻起始净利润_亿", "预测净利润_亿"):
        value = number(row.get(column))
        if value is None or value <= 0:
            return False
    start, end = number(row.get("前瞻起始年")), number(row.get("预测年"))
    if start is None or end is None or not start.is_integer() or not end.is_integer() or end <= start:
        return False
    base = number(row.get("基期净利润_亿"))
    return "基期净利润_亿" not in row or (base is not None and base > 0)


def _stress(row):
    start, end = number(row.get("前瞻起始净利润_亿")), number(row.get("预测净利润_亿"))
    first, last = number(row.get("前瞻起始年")), number(row.get("预测年"))
    if not start or start <= 0 or not end or end <= 0 or first is None or last is None or last <= first:
        return None, None
    growth = ((end * .8 / start) ** (1 / (last - first)) - 1) * 100
    pe = number(row.get("前瞻PE"))
    return round(growth, 2), round(pe / growth, 2) if pe and pe > 0 and growth > 0 else None


def assess_csi300_frame(frame, as_of_date=None):
    result = frame.drop(columns=list(LEGACY_COLUMNS), errors="ignore").copy().reset_index(drop=True)
    for column in ("代码", "名称"):
        if column not in result:
            result[column] = ""
    result["代码"] = result["代码"].map(_clean_code)
    invalid_codes = ~result["代码"].str.fullmatch(r"\d{6}") | result["代码"].duplicated(keep=False)
    as_of = (pd.Timestamp(as_of_date).date() if as_of_date is not None
             else pd.Timestamp.now(tz="Asia/Shanghai").date())
    rows = []
    for index, source in result.iterrows():
        row = source.to_dict()
        financial = _financial(row)
        industry_known = any(pd.notna(row.get(column)) and bool(str(row.get(column)).strip())
                             for column in ("行业", "历史一级行业"))
        row["比较组"] = ("金融" if financial else "非金融") if industry_known else "待核实"
        row["评估日期"] = as_of.isoformat()
        forecast_valid = _forecast_valid(row)
        row["压力CAGR_%"], row["压力PEG"] = _stress(row) if forecast_valid else (None, None)
        row["采用PEG"] = number(row.get("前瞻PEG")) if forecast_valid else None
        row["采用口径"] = "前瞻PEG" if row["采用PEG"] is not None else "不适用"
        count = number(row.get("机构数"))
        divergence = number(row.get("预测分歧度_%"))
        age = _report_age(row.get("报告截止"), as_of)
        volatility = number(row.get("历史波动率_%"))
        cash = [number(row.get(column)) for column in (
            "自由现金流_亿", "平均FCF收益率_%", "利润现金兑现率", "净负债_亿", "总市值_亿")]
        cash_complete = all(value is not None for value in cash)
        tags = []
        if invalid_codes.loc[index]:
            tags.append("股票代码缺失、无效或重复")
        if not industry_known:
            tags.append("行业数据缺失，分组待核实")
        if count is None or count < 3:
            tags.append("机构覆盖不足")
        if divergence is None:
            tags.append("预测分歧数据缺失")
        elif divergence > 40:
            tags.append("预测分歧过高")
        elif divergence > 25:
            tags.append("预测分歧偏高")
        if age is None:
            tags.append("报告日期缺失")
        elif age < 0:
            tags.append("报告日期晚于评估日")
        elif age > 180:
            tags.append("预测报告偏旧")
        if not financial:
            if not cash_complete:
                tags.append("现金流数据缺失")
            if cash[0] is not None and cash[0] <= 0:
                tags.append("自由现金流为负")
            if cash[3] is not None and cash[4] is not None and cash[4] > 0 and cash[3] / cash[4] > .5:
                tags.append("净负债偏高")
        if volatility is None:
            tags.append("波动率数据缺失")
        elif volatility > 50:
            tags.append("历史波动偏高")
        if str(row.get("基期异常")) == "是":
            tags.append("历史基期异常")
        if number(row.get("PE_TTM")) is not None and number(row.get("PE_TTM")) < 0:
            tags.append("当前TTM亏损")
        if row["压力CAGR_%"] is None:
            tags.append("压力情景不可计算")
        elif row["压力CAGR_%"] <= 0:
            tags.append("压力情景下增长转负")

        forecast_confidence = (.4 * _scale(count, 2, 15) + .4 * _scale(divergence, 30, 5)
                               + .2 * _scale(age if age is not None and age >= 0 else None, 180, 30))
        fields = [count, divergence, age, volatility, number(row.get("前瞻PEG")), number(row.get("前瞻CAGR_%"))]
        if not financial:
            fields.extend(cash)
        confidence = forecast_confidence * .7 + sum(value is not None for value in fields) / len(fields) * 30
        hard_low = (count is None or count < 3 or divergence is None or divergence > 40
                    or age is None or age < 0 or age > 180)
        row["数据可信度分"] = round(confidence, 2)
        row["可信度等级"] = "低" if hard_low or confidence < 55 else "高" if confidence >= 75 else "中"
        price, cap = number(row.get("最新价")), number(row.get("总市值_亿"))
        if row.get("状态") != "成功":
            reason = str(row.get("失败原因") or "原因待核实")
            if reason.startswith("盈利预测不可计算:"):
                row["筛选状态"] = "不适用"
                tags.append(reason)
            elif reason == "无可用盈利预测数据":
                row["筛选状态"] = "数据缺失"
                tags.append("预测数据缺失，是否无覆盖或接口异常需另行核实")
            else:
                row["筛选状态"] = "采集失败"
                tags.append("数据采集失败: " + reason)
        elif invalid_codes.loc[index]:
            row["筛选状态"] = "数据缺失"
        elif price is None or price <= 0 or cap is None or cap <= 0:
            row["筛选状态"] = "数据缺失"
            tags.append("实时行情数据缺失或无效")
        elif not forecast_valid:
            forecast_fields = ("前瞻PEG", "前瞻PE", "前瞻CAGR_%", "前瞻起始净利润_亿", "预测净利润_亿", "前瞻起始年", "预测年")
            row["筛选状态"] = "数据缺失" if any(number(row.get(column)) is None for column in forecast_fields) else "不适用"
            tags.append("前瞻估值、增长或预测利润无效，PEG不适用")
        elif not financial and not cash_complete:
            row["筛选状态"] = "数据缺失"
        elif row["可信度等级"] == "低" or not industry_known:
            row["筛选状态"] = "观察"
        else:
            row["筛选状态"] = "合格"
        row["风险标签"] = "；".join(dict.fromkeys(tags)) or "无显著数据风险"
        rows.append(row)
    result = pd.DataFrame(rows, index=result.index) if rows else result
    for column in ASSESSMENT_COLUMNS:
        if column not in result:
            result[column] = None
    result["组内PEG排名"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["组内样本数"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    eligible = result[result["筛选状态"].eq("合格")]
    for _, group in eligible.groupby("比较组", sort=False):
        ordered = group.assign(_peg=pd.to_numeric(group["前瞻PEG"])).sort_values(["_peg", "代码"], kind="stable")
        result.loc[ordered.index, "组内PEG排名"] = range(1, len(ordered) + 1)
        result.loc[ordered.index, "组内样本数"] = len(ordered)
    return result
