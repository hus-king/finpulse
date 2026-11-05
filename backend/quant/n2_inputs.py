"""Dated, bounded N2 inputs; frozen research files are read-only.

PEG formula and eligibility use the pinned Stock-Analyzer engine. Causal
statement/forecast primitives mirror its research adapter; unlike publication
reconstruction, provider create_time is retained and filtered here.
"""
import argparse
import bisect
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time

import pandas as pd
import requests

from integrations.stock_analyzer import analyzer
from integrations.stock_analyzer.selection import assess_csi300_frame

INPUT_VERSION = 'n2-pit-inputs-v1'
HISTORY_SESSIONS = 400
ENGINE_HASHES = {'analyzer.py':'f1bee4e8d21f86c79ebcee21c92b2a7dd4884d459aef02fab7ad5963297e60bc',
                 'selection.py':'81be76c054ed0a778818eeda496e4d744ec83608c88c040678d0302903ae9f02'}


class N2InputError(ValueError):
    pass


def _is_financial(industry):
    return any(word in str(industry or '') for word in ('银行','保险','证券','多元金融','金融'))



# Frozen causal selector source backtest_scoring.py sha256=2bef8f4e6e11c17de2aca9187e5095961ada50fc8c8688d07ff39ca372f9fd29

def day(value):
    text = str(value or "")[:10].replace("-", "")
    return text if len(text) == 8 and text.isdigit() else ""


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def announcement(row):
    return max(day(row.get("ann_date")), day(row.get("f_ann_date")))


def select_statements(rows, signal, periods=None):
    candidates = defaultdict(list)
    for row in rows:
        published = announcement(row)
        period = day(row.get("end_date"))
        if not published or published >= signal or period >= signal or str(row.get("report_type")) != "1":
            continue
        if periods is not None and period not in periods:
            continue
        candidates[period].append(row)
    selected = {}
    for period, versions in candidates.items():
        latest = max(announcement(row) for row in versions)
        versions = [row for row in versions if announcement(row) == latest]
        previous = versions[0]
        ignored = {"ann_date", "f_ann_date", "update_flag"}
        for row in versions[1:]:
            if any(previous.get(key) is not None and row.get(key) is not None and previous[key] != row[key] for key in (previous.keys() | row.keys()) - ignored):
                raise ValueError("同公告日财报版本冲突")
            previous = {key: previous.get(key) if previous.get(key) is not None else row.get(key) for key in previous.keys() | row.keys()}
        selected[period] = previous
    return selected


def visible_periods(rows, signal):
    return {day(row.get("end_date")) for row in rows if str(row.get("report_type")) == "1" and announcement(row) and announcement(row) < signal and day(row.get("end_date")) < signal}


def industry_label(row):
    level1 = str(row.get("l1_name") or "")
    level2 = str(row.get("l2_name") or "")
    return level1 if _is_financial(level1) and not _is_financial(level2) else level2 or level1


def forecast_frame(rows, signal, base_year, base_profit):
    cutoff = (pd.Timestamp(signal) - pd.Timedelta(days=180)).strftime("%Y%m%d")
    grouped = defaultdict(dict)
    conflicts = set()
    created = {}
    for row in rows:
        report = day(row.get("report_date"))
        ingest = day(row.get("create_time"))
        institution = str(row.get("org_name") or "").strip()
        if not institution or not cutoff <= report < signal or (ingest and ingest >= signal):
            continue
        key = (institution, report, str(row.get("report_title") or ""))
        grouped.setdefault(key, {})
        created[key] = max(created.get(key, ""), str(row.get("create_time") or ""))
        quarter = str(row.get("quarter") or "")
        if len(quarter) != 6 or not quarter.endswith("Q4") or not quarter[:4].isdigit():
            continue
        year = int(quarter[:4])
        profit = number(row.get("np"))
        if (key, year) in conflicts:
            continue
        if year in grouped[key] and grouped[key][year] != profit:
            grouped[key][year] = None
            conflicts.add((key, year))
        else:
            grouped[key][year] = profit
    latest = {}
    for key in grouped:
        institution, report, title = key
        if institution not in latest or (report, created[key], title) > (latest[institution][1], created[latest[institution]], latest[institution][2]):
            latest[institution] = key
    result = []
    for key in latest.values():
        years = grouped[key]
        if not all(years.get(base_year + offset) is not None and years[base_year + offset] > 0 for offset in (1, 2, 3)):
            continue
        record = {"机构": key[0], "报告日期": pd.Timestamp(key[1]), f"{base_year}_净利润(元)": base_profit}
        record.update({f"{base_year + offset}E_预测净利润(元)": years[base_year + offset] * 10000 for offset in (1, 2, 3)})
        result.append(record)
    return pd.DataFrame(result)


def factor_at(data, date):
    dates = data.get("factor_dates")
    if dates is None:
        dates = sorted(data["factors"])
    position = bisect.bisect_right(dates, date) - 1
    return number(data["factors"][dates[position]]) if position >= 0 else None


def adjusted_price(data, date, field):
    price = number(data["prices"].get(date, {}).get(field))
    factor = factor_at(data, date)
    return price * factor if price is not None and price > 0 and factor is not None and factor > 0 else None


def annual_rows(statements):
    return {period: row for period, row in statements.items() if period.endswith("1231")}


def balance_asof(data, signal):
    if data["balance"]:
        periods = visible_periods(data["balance"], signal)
        balances = select_statements(data["balance"], signal, {max(periods)} if periods else set())
        if not balances:
            raise ValueError("缺已公告季度资产负债表")
        row = balances[max(balances)]
        mapping = {"money_cap": "MONETARYFUNDS", "st_borr": "SHORT_LOAN", "lt_borr": "LONG_LOAN", "bond_payable": "BOND_PAYABLE", "non_cur_liab_due_1y": "NONCURRENT_LIAB_1YEAR", "st_bonds_payable": "SHORT_BOND_PAYABLE", "st_fin_payable": "SHORT_FIN_PAYABLE", "lease_liab": "LEASE_LIAB"}
        if number(row.get("money_cap")) is None:
            raise ValueError("资产负债表货币资金缺失")
        return {target: row.get(source) for source, target in mapping.items()}, "Tushare", day(row["end_date"])
    fallback = data["fallback"]
    rows = fallback.get("rows", []) if isinstance(fallback, dict) else fallback
    known = [row for row in rows if day(row.get("REPORT_DATE")) < signal and day(row.get("NOTICE_DATE")) and day(row["NOTICE_DATE"]) < signal]
    if not known:
        raise ValueError("缺已公告季度资产负债表")
    row = max(known, key=lambda value: day(value["REPORT_DATE"]))
    if not day(row.get("UPDATE_DATE")) or day(row["UPDATE_DATE"]) >= signal:
        raise ValueError("资产负债表只有事后修订版本")
    if number(row.get("MONETARYFUNDS")) is None:
        raise ValueError("资产负债表货币资金缺失")
    return row, "Eastmoney", day(row["REPORT_DATE"])


# Frozen causal selector source peg_policy_data.py sha256=4b3b6f882b225370bb6017a01f9b31cbf1c4981b4054bf1a2fd840eebfa12633

def rank_record(stock, data, cutoff, price_day, basic):
    """Only fields consumed by selection.py; omit unrelated shareholder-return gates."""
    industries = [r for r in data['industry'] if day(r.get('in_date')) and day(r['in_date']) < cutoff
                  and (not day(r.get('out_date')) or day(r['out_date']) > cutoff)]
    if not industries:
        raise ValueError('历史行业缺失')
    industry_row = max(industries, key=lambda r: day(r['in_date']))
    industry = industry_label(industry_row)
    periods = visible_periods(data['income'], cutoff)
    annual = {p for p in periods if p.endswith('1231')}
    if not annual:
        raise ValueError('缺已公告年报利润')
    base_year = int(max(annual)[:4]); latest = max(periods)
    year, quarter = int(latest[:4]), latest[4:]
    annual_needed = ({max(annual)} if _is_financial(industry)
                     else {f'{y}1231' for y in range(base_year - 2, base_year + 1)})
    required = annual_needed | {latest, f'{year-1}1231', f'{year-1}{quarter}'}
    income = select_statements(data['income'], cutoff, required)
    base_profit = number(income.get(f'{base_year}1231', {}).get('n_income_attr_p'))
    if not base_profit or base_profit <= 0:
        raise ValueError('基期年报利润非正或缺失')
    forecasts = forecast_frame(data['forecasts'], cutoff, base_year, base_profit)
    if len(forecasts) < 3:
        raise ValueError('对齐基期后完整预测机构不足3家')
    price, cap = float(basic['close']), float(basic['total_mv']) * 10000
    pe = number(basic.get('pe_ttm'))
    if pe is None:
        last = max(income); y, q = int(last[:4]), last[4:]
        needed = [last] if q == '1231' else [last, f'{y-1}1231', f'{y-1}{q}']
        profits = [number(income.get(p, {}).get('n_income_attr_p')) for p in needed]
        if all(p is not None for p in profits):
            ttm = profits[0] if len(profits) == 1 else profits[0] + profits[1] - profits[2]
            if ttm > 0:
                pe = cap / ttm
    metrics = analyzer.calculate_analysis(forecasts, pe_ttm=pe, market_cap=cap)
    if metrics['forward_peg'] is None or metrics['forward_cagr_percent'] <= 0:
        raise ValueError('前瞻增长非正或PEG无效')
    end = bisect.bisect_right(data['price_dates'], price_day)
    history = [adjusted_price(data, d, 'close') for d in data['price_dates'][max(0, end-253):end]]
    if len(history) < 121 or any(v is None for v in history):
        raise ValueError('历史复权价格不足120个收益观察')
    volatility = float(pd.Series(history).pct_change().dropna().std(ddof=1) * math.sqrt(252) * 100)
    row = {'代码':stock[:6], '名称':industry_row.get('name') or stock, '行业':industry,
           '历史一级行业':industry_row.get('l1_name'), '状态':'成功',
           '最新价':price, '总市值_亿':cap/1e8, 'PE_TTM':pe,
           '基期净利润_亿':base_profit/1e8, '预测年':base_year+3,
           '预测净利润_亿':metrics['future_net_profit']/1e8, '前瞻起始年':base_year+1,
           '前瞻起始净利润_亿':metrics['forward_start_net_profit']/1e8,
           '前瞻CAGR_%':metrics['forward_cagr_percent'], '前瞻PE':metrics['forward_pe'],
           '前瞻PEG':metrics['forward_peg'], '机构数':len(forecasts),
           '预测分歧度_%':metrics['divergence']*100, '报告截止':metrics['report_end_date'],
           '基期异常':'是' if metrics['abnormal_base'] else '否', '历史波动率_%':volatility}
    if not _is_financial(industry):
        cashflows = annual_rows(select_statements(data['cashflow'], cutoff, annual_needed))
        cashrows = []
        for y in range(base_year-2, base_year+1):
            period = f'{y}1231'; cf = cashflows.get(period, {})
            profit = number(income.get(period, {}).get('n_income'))
            if any(number(cf.get(k)) is None for k in ('n_cashflow_act','c_pay_acq_const_fiolta')) or profit is None:
                raise ValueError('三年现金流或同期净利润缺失')
            cashrows.append(dict(REPORT_DATE=f'{y}-12-31',NETCASH_OPERATE=cf['n_cashflow_act'],
                                 CONSTRUCT_LONG_ASSET=cf['c_pay_acq_const_fiolta'],NETPROFIT=profit))
        balance, _, _ = balance_asof(data, cutoff)
        # Dividend coverage is not an eligibility input. Its unavailable value stays None.
        cash = analyzer.calculate_cash_return_quality(cashrows,balance,{'total_return':None},cap,pe,metrics['cagr_percent'])
        if cash is None:
            raise ValueError('现金回报质量无法计算')
        row.update({'自由现金流_亿':cash['free_cash_flow_total']/1e8,
                    '平均FCF收益率_%':cash['free_cash_flow_yield_percent'],
                    '利润现金兑现率':cash['cash_conversion'], '净负债_亿':cash['net_debt']/1e8})
    return row


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _iso(value):
    value = day(value)
    if not value:
        raise N2InputError('无效的输入日期')
    return date.fromisoformat(value[:4]+'-'+value[4:6]+'-'+value[6:]).isoformat()


def _engine_proof():
    root = Path(analyzer.__file__).parent
    actual = {name:_sha(root/name) for name in ENGINE_HASHES}
    if actual != ENGINE_HASHES:
        raise N2InputError('N2使用的冻结PEG公式版本不匹配')
    return actual


def _atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp-'+str(os.getpid())+'-'+str(threading.get_ident()))
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    temporary.chmod(0o600)
    temporary.replace(path)


def _read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def _merge_rows(base, overlay, relative, proof=None, *, required=True):
    """Keep distinct dated versions. Overlays never overwrite the source archive."""
    result = {}
    found = False
    for label, root in [('base', Path(base)), ('overlay', Path(overlay))]:
        path = root/relative
        if not path.is_file():
            continue
        found = True
        rows = _read(path)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise N2InputError('档案字段格式无效：'+str(relative))
        if proof is not None:
            proof[label+'/'+str(relative)] = _sha(path)
        for row in rows:
            result[json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False)] = row
    if required and not found:
        raise N2InputError('缺少股票档案：'+str(relative))
    return list(result.values())


def _dated(rows, field, stock=None):
    result = {}
    for row in rows:
        stamp = day(row.get(field))
        if not stamp or stock and row.get('ts_code') != stock:
            raise N2InputError('行情日期或证券代码不匹配')
        prior = result.get(stamp)
        if prior is not None:
            overlap = set(prior)&set(row)
            if any(prior[k] is not None and row[k] is not None and prior[k] != row[k] for k in overlap):
                raise N2InputError('同一日期行情版本冲突：'+str(stock or '')+' '+stamp)
            row = {k:prior.get(k) if prior.get(k) is not None else row.get(k) for k in set(prior)|set(row)}
        result[stamp] = row
    return result


def _memberships(base, overlay, proof=None):
    snapshots = {}
    for label, root in [('base', Path(base)), ('overlay', Path(overlay))]:
        path = root/'membership_summary.json'
        saved = _read(path, {})
        if not isinstance(saved, dict):
            raise N2InputError('成分股快照格式无效')
        if path.exists() and proof is not None:
            proof[label+'/membership_summary.json'] = _sha(path)
        for item in saved.values():
            stamp = day(item.get('trade_date'))
            stocks = item.get('stocks')
            if not stamp or not isinstance(stocks, list) or len(stocks) != 300 or len(set(stocks)) != 300:
                raise N2InputError('沪深300成分股快照不完整')
            if any(not re.fullmatch(r'[036]\d{5}\.(SH|SZ)', code) for code in stocks):
                raise N2InputError('成分股证券代码无效')
            normalized = sorted(stocks)
            if stamp in snapshots and snapshots[stamp] != normalized:
                raise N2InputError('成分股快照版本冲突')
            snapshots[stamp] = normalized
    return snapshots


def _stock(base, overlay, stock, proof, *, fundamentals=True):
    values = {name:_merge_rows(base, overlay, Path(name)/(stock+'.json'), proof,
                              required=fundamentals or name in ('daily','adj_factor','daily_basic','industry'))
              for name in ('daily','adj_factor','daily_basic','income','cashflow','balancesheet','report_rc','industry')}
    prices = _dated(values['daily'], 'trade_date', stock)
    factors = _dated(values['adj_factor'], 'trade_date', stock)
    basics = _dated(values['daily_basic'], 'trade_date', stock)
    return dict(prices=prices, price_dates=sorted(prices),
                factors={d:r.get('adj_factor') for d,r in factors.items()}, factor_dates=sorted(factors),
                basics=basics, income=values['income'], cashflow=values['cashflow'],
                balance=values['balancesheet'], forecasts=values['report_rc'], industry=values['industry'], fallback={})


def _bar(row, stamp, factor=1.):
    volume = number(row.get('vol'))
    values = {key:number(row.get(key)) for key in ('open','high','low','close')}
    # Suspended sessions can contain a real reference close and zero O/H/L.
    # Retain the source close for features, never turn this into a fill price.
    if (volume == 0 and values['close'] is not None and values['close'] > 0
            and all(values[k] == 0 for k in ('open','high','low'))):
        return dict(date=_iso(stamp), open=0., high=0., low=0.,
                    close=values['close']*factor, volume=0., factor=factor, tradable=False)
    if any(value is None or value <= 0 for value in values.values()):
        raise N2InputError('实际日线OHLC缺失或非正：'+stamp)
    if values['low'] > min(values['open'],values['close']) or values['high'] < max(values['open'],values['close']):
        raise N2InputError('实际日线OHLC范围不一致：'+stamp)
    if volume is None or volume < 0:
        raise N2InputError('日线成交量缺失或为负：'+stamp)
    return dict(date=_iso(stamp), **{key:value*factor for key,value in values.items()},
                volume=volume*100., factor=factor)


def _stock_code(value):
    value = str(value)
    if re.fullmatch(r'[036]\d{5}', value):
        return value+('.SH' if value.startswith('6') else '.SZ')
    if not re.fullmatch(r'[036]\d{5}\.(SH|SZ)', value):
        raise N2InputError('无效证券代码')
    return value


def load_inputs(base_root, overlay_root, decision_day, *, calendar=None, extra_codes=()):
    """Build a complete, dated cross-section; only execution bars contain day t.

    ``histories`` and ``calendar`` end at t-1. ``daily`` additionally contains t's
    actual OHLC for the explicitly approved daily-open execution approximation.
    It must never be fed to the ranking function. All 300 constituents receive a
    coverage row, including legitimate forecast/eligibility failures.
    """
    base, overlay = Path(base_root), Path(overlay_root)
    decision = day(decision_day)
    _iso(decision)
    proof = {}
    engine = _engine_proof()
    snapshots = _memberships(base, overlay, proof)
    known = [d for d in snapshots if d < decision]
    if not known:
        raise N2InputError('缺少决策日以前可见的300股成分快照')
    membership_day = max(known)
    members = snapshots[membership_day]
    index = _dated(_merge_rows(base, overlay, Path('reference/csi300_price.json'), proof), 'trade_date')
    dates = sorted({day(d) for d in calendar} if calendar is not None else index)
    if not dates or any(not d for d in dates):
        raise N2InputError('缺少可核验的交易日历')
    past = [d for d in dates if d < decision]
    if not past:
        raise N2InputError('决策前缺少完整交易日')
    signal = past[-1]
    # A known calendar is required when the index archive stops at yesterday.
    from ..trading_calendar import is_trade_day
    verified = is_trade_day(date.fromisoformat(_iso(decision)))
    if verified is False or verified is None and decision not in dates:
        raise N2InputError('决策日不是已核验交易日')
    manifest_path = base/'data_manifest.json'
    manifest = _read(manifest_path, {})
    if manifest_path.exists():
        proof['base/data_manifest.json'] = _sha(manifest_path)
    if day(manifest.get('end')) < signal:
        refresh_path = overlay/'refresh-state.json'
        refresh = _read(refresh_path, {})
        if refresh.get('status') != 'complete' or day(refresh.get('end')) < signal or refresh.get('failed_stocks'):
            raise N2InputError('新增交易日的原始档案尚未完整更新')
        proof['overlay/refresh-state.json'] = _sha(refresh_path)
    first = past[max(0,len(past)-HISTORY_SESSIONS)]
    records, histories, daily, coverage, dividends, sectors = [], {}, {}, [], {}, {}
    missing_forecast_vintage = 0
    report_start = day(date.fromisoformat(_iso(decision))-timedelta(days=180))
    for stock in sorted(set(members)|{_stock_code(code) for code in extra_codes}):
        data = _stock(base, overlay, stock, proof, fundamentals=stock in members)
        code = stock[:6]
        missing_forecast_vintage += sum(bool(report_start<=day(row.get('report_date'))<decision)
                                       and not day(row.get('create_time')) for row in data['forecasts'])
        known_industries = [row for row in data['industry'] if day(row.get('in_date')) < decision
                            and day(row.get('in_date')) and (not day(row.get('out_date')) or day(row['out_date']) > decision)]
        sectors[code] = max(known_industries, key=lambda row:day(row['in_date'])).get('l1_name') if known_industries else None
        raw, adjusted = [], []
        execution = {}
        for stamp in data['price_dates']:
            if not first <= stamp <= decision:
                continue
            execution[_iso(stamp)] = _bar(data['prices'][stamp], stamp)
            execution[_iso(stamp)]['factor'] = number(data['factors'].get(stamp))
            if stamp > signal:
                continue
            factor = number(data['factors'].get(stamp))
            if factor is None or factor <= 0:
                raise N2InputError(stock+' 实际行情缺少当时复权因子：'+stamp)
            raw.append({**_bar(data['prices'][stamp], stamp),'factor':factor})
            adjusted.append(_bar(data['prices'][stamp], stamp, factor))
        histories[code] = {'raw':raw,'adjusted':adjusted}
        daily[code] = execution
        dividends[code] = [row for row in _merge_rows(base, overlay, Path('dividend')/(stock+'.json'), proof, required=False)
                           if day(row.get('ann_date')) and day(row['ann_date']) < decision]
        if stock not in members:
            continue
        try:
            bar = data['prices'].get(signal, {})
            basic = data['basics'].get(signal, {})
            if (number(bar.get('vol')) or 0) <= 0:
                raise ValueError('前一交易日无成交行情')
            if not number(basic.get('total_mv')) or not number(basic.get('total_share')):
                raise ValueError('前一交易日市值或股本缺失')
            if abs(float(basic['close'])/float(bar['close'])-1) > 1e-5:
                raise ValueError('估值与日线收盘价不一致')
            if abs(float(basic['total_mv'])/(float(basic['close'])*float(basic['total_share']))-1) > 1e-4:
                raise ValueError('市值与股本单位不一致')
            record = rank_record(stock, data, decision, signal, basic)
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            record = {'代码':code,'名称':stock,'状态':'失败','失败原因':str(error)}
        record.update(stock=stock, price_date=signal, membership_asof=membership_day)
        records.append(record)
    assessed = assess_csi300_frame(pd.DataFrame(records), as_of_date=decision)
    rows = []
    for record in json.loads(assessed.to_json(orient='records', force_ascii=False)):
        code = record['代码']
        coverage.append(dict(code=code, name=record.get('名称'), status=record['筛选状态'],
                             reason=record.get('失败原因') or record.get('风险标签')))
        if record['筛选状态'] == '合格':
            rows.append(dict(code=code, name=record['名称'], peg=record['前瞻PEG'],
                             industry_l1=record.get('历史一级行业'), group=record['比较组'],
                             status='合格', last_report=record.get('报告截止')))
    if len(coverage) != 300 or len({r['code'] for r in coverage}) != 300:
        raise N2InputError('因子输入未覆盖全部300股')
    return dict(version=INPUT_VERSION, date=_iso(decision), signal_date=_iso(signal),
        membership_date=_iso(membership_day), rows=rows, histories=histories,
        calendar=[_iso(d) for d in past], daily=daily, dividends=dividends, sectors=sectors, coverage=coverage,
        provenance=dict(source_hashes=proof, engine_hashes=engine, source_members=members,
            assembled_at=datetime.now(timezone.utc).isoformat(),
            fetched_at=_read(overlay/'refresh-state.json', {}).get('finished_at'),
            cutoff='report/create/statement announcement strictly before decision day; histories through previous session',
            forecast_availability='strict provider create_time retained',
            forecast_missing_create_time_rows=missing_forecast_vintage,
            coverage=dict(Counter(r['status'] for r in coverage)),
            history_sessions=HISTORY_SESSIONS, history_price_basis='raw multiplied by cumulative causal adj_factor',
            daily_volume_unit='shares; provider daily vol multiplied by100',
            historical_vintage_limit='supplier historical in-place revisions cannot be fully excluded'))


FIELDS = {
    'daily':'ts_code,trade_date,open,high,low,close,vol,amount',
    'adj_factor':'ts_code,trade_date,adj_factor',
    'daily_basic':'ts_code,trade_date,close,pe_ttm,total_share,total_mv',
    'income':'ts_code,ann_date,f_ann_date,end_date,report_type,n_income,n_income_attr_p',
    'cashflow':'ts_code,ann_date,f_ann_date,end_date,report_type,n_cashflow_act,c_pay_acq_const_fiolta',
    'balancesheet':'ts_code,ann_date,f_ann_date,end_date,report_type,money_cap,st_borr,lt_borr,bond_payable,non_cur_liab_due_1y,st_bonds_payable,st_fin_payable,lease_liab',
    'report_rc':'ts_code,report_date,report_title,org_name,quarter,np,create_time',
    'industry':'l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,name,in_date,out_date,is_new',
    'dividend':'ts_code,end_date,ann_date,div_proc,stk_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate',
    'stk_limit':'ts_code,trade_date,up_limit,down_limit',
    'index_daily':'ts_code,trade_date,open,close,high,low,vol,amount',
    'index_weight':'index_code,con_code,trade_date,weight',
}


class _Provider:
    """Two callers share a two-request/second gate and any provider cooldown."""
    def __init__(self, config):
        self.url = config.get('http_url') or config.get('base_url')
        self.token = config.get('token')
        if not isinstance(self.url, str) or not self.url.startswith(('http://','https://')) or not self.token:
            raise N2InputError('N2数据源配置缺少接口地址或令牌')
        self.lock = threading.Lock()
        self.next = 0.
        self.cooldown_until = 0.
        self.abort_event = threading.Event()
        self.calls = 0

    def _dispatch_slot(self, api):
        while True:
            with self.lock:
                if self.abort_event.is_set():
                    raise N2InputError(api+': provider_429_aborted')
                now = time.monotonic()
                remaining = max(self.next,self.cooldown_until)-now
                if remaining <= 0:
                    self.next = now+.5
                    self.calls += 1
                    return
            # Recheck the shared cooldown after every wake, including when the
            # other thread receives429 while this request waits for its slot.
            if self.abort_event.wait(remaining):
                raise N2InputError(api+': provider_429_aborted')

    @staticmethod
    def _retry_after(response):
        value = getattr(response,'headers',{}).get('Retry-After')
        try:
            seconds = float(value)
        except (TypeError,ValueError):
            try:
                stamp = parsedate_to_datetime(str(value))
                stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
                seconds = (stamp-datetime.now(timezone.utc)).total_seconds()
            except (TypeError,ValueError,OverflowError):
                seconds = 5.
        return min(60.,max(5.,seconds)) if math.isfinite(seconds) else 5.

    def query(self, api, params, fields):
        for attempt in range(2):
            self._dispatch_slot(api)
            try:
                response = requests.post(self.url, json=dict(api_name=api, token=self.token, params=params, fields=fields), timeout=35)
                payload = {'code':429} if getattr(response,'status_code',None)==429 else response.json()
            except (requests.RequestException, ValueError) as error:
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise N2InputError(api+': '+type(error).__name__) from None
            if not isinstance(payload,dict) or type(payload.get('code')) is not int:
                raise N2InputError(api+': provider_schema_invalid')
            code = payload['code']
            if code == 429:
                with self.lock:
                    self.cooldown_until = max(self.cooldown_until,time.monotonic()+self._retry_after(response))
                    if attempt:
                        self.abort_event.set()
                if attempt:
                    raise N2InputError(api+': provider_code_429; refresh_aborted')
                continue
            if code in (500,502,503,504) and attempt == 0:
                time.sleep(2)
                continue
            if code != 0:
                raise N2InputError(api+': provider_code_'+str(code))
            data = payload.get('data') or {}
            columns, items = data.get('fields') or [], data.get('items') or []
            if not isinstance(columns,list) or not isinstance(items,list) or any(not isinstance(row,list) or len(row)!=len(columns) for row in items):
                raise N2InputError(api+': provider_schema_invalid')
            return [dict(zip(columns,row)) for row in items]
        raise N2InputError(api+': request_failed')

    def ranged(self, api, params, fields, cap=3000):
        rows = self.query(api, params, fields)
        if len(rows) < cap:
            return rows
        start, end = date.fromisoformat(_iso(params['start_date'])), date.fromisoformat(_iso(params['end_date']))
        if start >= end:
            raise N2InputError(api+': single_day_row_cap')
        middle = start+(end-start)//2
        return (self.ranged(api, {**params,'end_date':day(middle)}, fields, cap)
                +self.ranged(api, {**params,'start_date':day(middle+timedelta(days=1))}, fields, cap))


def _saved_rows(base, overlay, relative):
    return _merge_rows(base, overlay, relative, required=False)


def _cache_fresh(overlay, relative, end):
    path, meta = overlay/relative, _read(overlay/'metadata'/relative, {})
    return bool(path.is_file() and day(meta.get('checked_through')) >= end and meta.get('rows_sha256') == _sha(path))


def _cache_rows(overlay, relative, rows, *, api, params, end):
    # Retain all distinguishable financial/forecast versions for strict filtering.
    old = _read(overlay/relative, [])
    combined = {json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False):row for row in old+rows}
    _atomic(overlay/relative, list(combined.values()))
    _atomic(overlay/'metadata'/relative, dict(api=api, params=params, fields=FIELDS.get(api,FIELDS.get('industry')),
        checked_through=end, fetched_at=datetime.now(timezone.utc).isoformat(), rows=len(rows),
        rows_sha256=_sha(overlay/relative)))


def _closed_months(after, end):
    current = date.fromisoformat(_iso(after)).replace(day=1)
    finish = date.fromisoformat(_iso(end))
    result = []
    while current <= finish:
        following = (current.replace(day=28)+timedelta(days=4)).replace(day=1)
        last = following-timedelta(days=1)
        if day(last) <= end:
            result.append((day(current),day(last)))
        current = following
    return result


def _refresh_archive(base_root, overlay_root, provider_config, end_day, *, extra_codes=()):
    """Incremental, resumable raw-data refresh; never writes into base_root.

    At most two threads and two requests/second are used. The caller runs this
    single Python process with its explicit address-space/resource limit. A
    partial refresh is recorded and cannot pass the new-session readiness gate.
    """
    base, overlay = Path(base_root).resolve(), Path(overlay_root).resolve()
    if base == overlay or base in overlay.parents or overlay in base.parents:
        raise N2InputError('新增数据必须使用独立overlay目录')
    end = day(end_day)
    _iso(end)
    overlay.mkdir(parents=True, exist_ok=True)
    provider = _Provider(provider_config)
    started = datetime.now(timezone.utc).isoformat()
    state_path = overlay/'refresh-state.json'
    state = dict(version=INPUT_VERSION, status='running', start_at=started, end=end, completed_stocks=0,
                 failed_stocks=0, failures=[], workers=2, request_rate=2,
                 base_manifest_sha256=_sha(base/'data_manifest.json') if (base/'data_manifest.json').exists() else None)
    _atomic(state_path,state)
    snapshots = _memberships(base, overlay)
    if not snapshots:
        raise N2InputError('缺少基础沪深300历史快照')
    # Monthly membership known before a decision; never backfill today's names.
    membership = _read(overlay/'membership_summary.json', {})
    for start, finish in _closed_months(max(snapshots), end):
        relative = Path('index_weight')/(start[:6]+'.json')
        if _cache_fresh(overlay,relative,end):
            rows = _read(overlay/relative, [])
        else:
            params = dict(index_code='000300.SH', start_date=start, end_date=finish)
            rows = provider.ranged('index_weight',params,FIELDS['index_weight'],5000)
            _cache_rows(overlay,relative,rows,api='index_weight',params=params,end=end)
        if not rows:
            raise N2InputError('已结束月份缺少成分股快照：'+start[:6])
        latest = max(day(row.get('trade_date')) for row in rows)
        stocks = sorted({row.get('con_code') for row in rows if day(row.get('trade_date'))==latest})
        if len(stocks) != 300 or any(not start<=day(row.get('trade_date'))<=finish for row in rows):
            raise N2InputError('新增成分股快照不完整或日期越界')
        membership[start[:6]] = dict(trade_date=latest, stocks=stocks)
    _atomic(overlay/'membership_summary.json',membership)
    snapshots = _memberships(base,overlay)
    latest_two = sorted(d for d in snapshots if d<=end)[-2:]
    stocks = sorted(set().union(*(set(snapshots[d]) for d in latest_two))|{_stock_code(c) for c in extra_codes})
    index_relative = Path('reference/csi300_price.json')
    index_rows = _saved_rows(base,overlay,index_relative)
    latest_index = max((day(r.get('trade_date')) for r in index_rows), default='20240101')
    if latest_index < end:
        params = dict(ts_code='000300.SH',start_date=day(date.fromisoformat(_iso(latest_index))+timedelta(days=1)),end_date=end)
        rows = provider.ranged('index_daily',params,FIELDS['index_daily'],6000)
        _cache_rows(overlay,index_relative,rows,api='index_daily',params=params,end=end)

    def stock_job(stock):
        for api in ('daily','adj_factor','daily_basic','report_rc','income','cashflow','balancesheet','industry','dividend','stk_limit'):
            if provider.abort_event.is_set():
                raise N2InputError('provider_429_aborted')
            relative = Path(api)/(stock+'.json')
            if _cache_fresh(overlay,relative,end):
                continue
            saved = _saved_rows(base,overlay,relative)
            if api in ('daily','adj_factor','daily_basic','stk_limit'):
                last = max((day(r.get('trade_date')) for r in saved),default='')
                if last >= end:
                    continue
                start = day(date.fromisoformat(_iso(last))+timedelta(days=1)) if last else '20240101'
                params = dict(ts_code=stock,start_date=start,end_date=end)
                rows = provider.ranged(api,params,FIELDS[api],6000)
                if any(row.get('ts_code') != stock or not start<=day(row.get('trade_date'))<=end for row in rows):
                    raise N2InputError(api+': provider_date_or_stock_mismatch')
            elif api == 'report_rc':
                start = day(date.fromisoformat(_iso(end))-timedelta(days=370))
                params = dict(ts_code=stock,start_date=start,end_date=end,limit=3000)
                rows = provider.ranged(api,params,FIELDS[api])
                if any(row.get('ts_code') != stock or not start<=day(row.get('report_date'))<=end for row in rows):
                    raise N2InputError(api+': provider_date_or_stock_mismatch')
            elif api == 'industry':
                params = dict(ts_code=stock)
                rows = provider.query('index_member_all',{**params,'is_new':'Y'},FIELDS['industry'])
                rows += provider.query('index_member_all',{**params,'is_new':'N'},FIELDS['industry'])
            else:
                params = dict(ts_code=stock)
                if api != 'dividend':
                    params.update(start_date=str(int(end[:4])-5)+'0101',end_date=end)
                rows = provider.query(api,params,FIELDS[api])
                if len(rows) >= 3000:
                    raise N2InputError(api+': statement_row_cap')
            if any(row.get('ts_code') != stock for row in rows):
                raise N2InputError(api+': provider_stock_mismatch')
            _cache_rows(overlay,relative,rows,api=api,params=params,end=end)
        return stock

    state['total_stocks'] = len(stocks)
    _atomic(state_path,state)
    with ThreadPoolExecutor(max_workers=2) as workers:
        remaining_stocks = iter(stocks)
        pending = {}
        def submit_next():
            if provider.abort_event.is_set():
                return
            stock = next(remaining_stocks,None)
            if stock is not None:
                pending[workers.submit(stock_job,stock)] = stock
        submit_next();submit_next()
        while pending:
            done,_ = wait(pending,return_when=FIRST_COMPLETED)
            for future in done:
                stock = pending.pop(future)
                try:
                    future.result()
                except Exception as error:
                    # Only controlled adapter messages or exception class escape.
                    reason = str(error) if isinstance(error,N2InputError) else type(error).__name__
                    state['failures'].append(dict(stock=stock,reason=reason))
                    state['failed_stocks'] += 1
                state['completed_stocks'] += 1
                if not provider.abort_event.is_set():
                    submit_next()
            if provider.abort_event.is_set():
                state['abort_reason'] = 'provider_429'
                for future in list(pending):
                    if future.cancel():
                        pending.pop(future)
                state['deferred_stocks'] = len(stocks)-state['completed_stocks']-len(pending)
            state['http_calls'] = provider.calls
            _atomic(state_path,state)
    state.update(status='failed' if state['failed_stocks'] else 'complete',
                 finished_at=datetime.now(timezone.utc).isoformat(), http_calls=provider.calls)
    _atomic(state_path,state)
    if state['failed_stocks']:
        raise N2InputError('N2原始数据更新未完成：'+str(state['failed_stocks'])+'只股票失败；已保存成功缓存')
    return state


def refresh_archive(base_root, overlay_root, provider_config, end_day, *, extra_codes=()):
    base, overlay = Path(base_root).resolve(), Path(overlay_root).resolve()
    if base == overlay or base in overlay.parents or overlay in base.parents:
        raise N2InputError('新增数据必须使用独立overlay目录')
    try:
        return _refresh_archive(base,overlay,provider_config,end_day,extra_codes=extra_codes)
    except Exception as error:
        path = overlay/'refresh-state.json'
        state = _read(path,{})
        if state.get('status') == 'running':
            state.update(status='failed',finished_at=datetime.now(timezone.utc).isoformat(),
                         error=str(error) if isinstance(error,N2InputError) else type(error).__name__)
            _atomic(path,state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('refresh','build'))
    for name in ('base','overlay'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--end')
    parser.add_argument('--date')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--extra-code',action='append',default=[])
    args = parser.parse_args()
    if args.mode == 'refresh':
        if not args.config or not args.end:
            parser.error('refresh requires --config and --end')
        result = refresh_archive(args.base,args.overlay,json.loads(args.config.read_text()),args.end,extra_codes=args.extra_code)
    else:
        if not args.date or not args.output:
            parser.error('build requires --date and --output')
        result = load_inputs(args.base,args.overlay,args.date,extra_codes=args.extra_code)
        _atomic(args.output,result)
        result = dict(version=result['version'],date=result['date'],signal_date=result['signal_date'],
                      eligible=len(result['rows']),coverage=result['provenance']['coverage'],output=str(args.output))
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()

