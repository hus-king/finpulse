"""Map the pinned Stock-Analyzer assessment into an immutable public snapshot."""
import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from integrations.stock_analyzer.selection import assess_csi300_frame
from .news_cleaning import SHANGHAI
from .trading_calendar import previous_trade_day

ENGINE_ROOT = Path(__file__).resolve().parents[1] / 'integrations' / 'stock_analyzer'
SOURCE = json.loads((ENGINE_ROOT / 'SOURCE.json').read_text())
ACTUAL_HASHES = {name:hashlib.sha256((ENGINE_ROOT/name).read_bytes()).hexdigest() for name in SOURCE['files']}
if ACTUAL_HASHES != SOURCE['files']:
    raise RuntimeError('Stock-Analyzer integration does not match its pinned source manifest')
METHOD_VERSION = 'forward-peg-v1-' + hashlib.sha256(json.dumps(ACTUAL_HASHES, sort_keys=True).encode()).hexdigest()[:16]
STATUS_KEYS = {'合格':'eligible', '观察':'observing', '不适用':'not_applicable', '数据缺失':'missing', '采集失败':'failed'}


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def text(value):
    return '' if value is None or isinstance(value, float) and not math.isfinite(value) else str(value)


def build_snapshot(records, *, now=None, started_at=None, previous=None, expected_total=300):
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if len(records) != expected_total or not records:
        raise ValueError('成分股批次不完整，保留上次榜单')
    codes = [str(r.get('代码','')).strip().zfill(6) for r in records]
    if len(set(codes)) != len(codes) or any(not re.fullmatch(r'[036]\d{5}', code) for code in codes):
        raise ValueError('成分股代码缺失、重复或无效')
    if any(not text(r.get('名称')).strip() for r in records):
        raise ValueError('成分股名称缺失')
    frame = assess_csi300_frame(pd.DataFrame(records), as_of_date=now.date().isoformat())
    # Engine nullable/numpy scalars become JSON primitives before public mapping.
    assessed = json.loads(frame.to_json(orient='records', force_ascii=False))
    assessed_codes = [r.get('代码') for r in assessed]
    if len(assessed_codes) != expected_total or len(set(assessed_codes)) != expected_total or set(assessed_codes) != set(codes):
        raise ValueError('筛选结果与成分股集合不一致，保留上次榜单')
    summary = dict(total=len(records), eligible=0, observing=0, not_applicable=0, missing=0, failed=0)
    prior_rows = {r['code']:r for r in (previous or {}).get('rows',[])}
    same_method = bool(previous and previous.get('method_version') == METHOD_VERSION)
    same_universe = same_method and set(prior_rows) == set(codes)
    rows = []
    for raw in assessed:
        code = raw['代码']; status = raw['筛选状态']; group = raw['比较组']
        rank = number(raw.get('组内PEG排名')); rank = int(rank) if rank is not None else None
        prior = prior_rows.get(code) if same_method else None
        old_rank = prior.get('rank') if prior and prior.get('group') == group else None
        if not same_method:
            change_status = 'initial'
        elif prior is None:
            change_status = 'new'
        elif prior.get('group') != group:
            change_status = 'group_changed'
        elif not same_universe:
            change_status = 'universe_changed'
        elif rank is not None and old_rank is None:
            change_status = 'entered'
        elif rank is None and old_rank is not None:
            change_status = 'exited'
        else:
            change_status = 'unchanged'
        delta = old_rank-rank if same_universe and old_rank is not None and rank is not None else None
        summary[STATUS_KEYS[status]] += 1
        rows.append({
            'code':code, 'name':text(raw.get('名称')), 'industry':text(raw.get('行业')) or '待核实',
            'group':group, 'status':status, 'rank':rank, 'previous_rank':old_rank,
            'rank_change':delta, 'rank_status':change_status,
            'price':number(raw.get('最新价')), 'peg':number(raw.get('前瞻PEG')),
            'forward_pe':number(raw.get('前瞻PE')), 'growth':number(raw.get('前瞻CAGR_%')),
            'pe_ttm':number(raw.get('PE_TTM')), 'market_cap':number(raw.get('总市值_亿')),
            'confidence':text(raw.get('可信度等级')), 'risk_tags':[t for t in text(raw.get('风险标签')).split('；') if t],
            'institutions':number(raw.get('机构数')), 'forecast_start':raw.get('报告起始'),
            'forecast_end':raw.get('报告截止'), 'collected_at':raw.get('采集时间'),
            'stress_peg':number(raw.get('压力PEG')), 'divergence':number(raw.get('预测分歧度_%')),
        })
    if summary['missing'] + summary['failed'] == len(rows):
        raise ValueError('本轮未取得可评估的数据，保留上次榜单')
    order = {'合格':0,'观察':1,'不适用':2,'数据缺失':3,'采集失败':4}
    rows.sort(key=lambda r:(order[r['status']], {'非金融':0,'金融':1}.get(r['group'],2),
                            r['rank'] if r['rank'] is not None else math.inf, r['code']))
    market_day = previous_trade_day(now.date(), include=now.hour >= 15)
    return {'snapshot_id':uuid.uuid4().hex, 'method_version':METHOD_VERSION,
            'previous_snapshot_id':(previous or {}).get('snapshot_id'), 'published_at':now.isoformat(),
            'as_of_date':now.date().isoformat(), 'collection_started_at':started_at or now.isoformat(),
            'collection_finished_at':now.isoformat(), 'market_date':market_day.isoformat() if market_day else None,
            'market_date_note':'交易日历对应日期；逐股源未提供成交时间，行情抓取时间另列。停牌股可能沿用更早成交价。',
            'summary':summary, 'rows':rows, 'source':{'project':SOURCE['project'],'method_version':METHOD_VERSION}}
