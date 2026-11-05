"""Pure rules for the frozen four-factor, two-stock inverse-volatility policy.

``as_of`` is the last complete session, not the execution day. The caller owns
data collection, price exits/fill persistence and execution-model provenance.
Adjusted history is multiplicative (raw OHLC times its causal Tushare factor).
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
import math
import re

import numpy as np
import pandas as pd


N2_STRATEGY_ID = 'official-four-factor-n2-v1'
N2_VERSION = 'four-factor-n2-invvol60-k9-v1'
N2_FACTORS = ('peg', 'momentum', 'trend', 'lowvol')
N2_WEIGHTS = (.351, .249, .15, .25)
N2_COUNT = 2
N2_EXIT_RANK = 9
N2_CONFIRM = 3
N2_STOP_LOSS = .18
N2_COOLDOWN = 20
N2_SECTOR_CAP = .6
N2_CANDIDATE_SECTOR_COUNT = 5
N2_BAND = .05


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('策略日期必须为YYYY-MM-DD')
    return value


def _calendar(calendar, as_of):
    _day(as_of)
    days = [_day(d) for d in calendar if d <= as_of]
    if not days or days[-1] != as_of or days != sorted(set(days)):
        raise ValueError('缺少有序且完整的信号日交易日历')
    return days


def _history_rows(history, as_of):
    """Discard future observations before checking actual raw/adjusted alignment."""
    try:
        raw = [b for b in history['raw'] if b['date'] <= as_of]
        adjusted = [b for b in history['adjusted'] if b['date'] <= as_of]
    except (KeyError, TypeError):
        raise ValueError('缺少原始或复权历史行情') from None
    dates = [_day(b['date']) for b in raw]
    if not dates or dates != [_day(b['date']) for b in adjusted] or dates != sorted(set(dates)):
        raise ValueError('原始和复权历史行情未按实际交易日对齐')
    for bars in (raw, adjusted):
        for bar in bars:
            value = bar.get('close')
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('原始或复权收盘价无效')
    return raw, adjusted


def _adjusted_close(history, calendar, as_of):
    _, adjusted = _history_rows(history, as_of)
    # Each actual price has already received its own factor. Carrying that
    # adjusted observation through a suspension avoids inventing a halt return.
    return pd.Series({b['date']: float(b['close']) for b in adjusted}, dtype=float).reindex(calendar).ffill()


def _finite(value):
    return float(value) if pd.notna(value) and math.isfinite(value) else None


def _technical_values(adjusted):
    returns = adjusted.pct_change(fill_method=None)
    momentum = adjusted.shift(21) / adjusted.shift(252) - 1
    volatility = returns.rolling(231, min_periods=231).std(ddof=1).shift(21) * np.sqrt(252)
    momentum = momentum / volatility.where(volatility > 0)
    return {
        'momentum': _finite(momentum.iloc[-1]),
        'trend': _finite((adjusted / adjusted.rolling(60, min_periods=60).mean() - 1).iloc[-1]),
        'lowvol': _finite((-returns.rolling(120, min_periods=120).std(ddof=1) * np.sqrt(252)).iloc[-1]),
    }


def _centered_rank(values):
    numeric = pd.Series(values, dtype=float).where(lambda s: np.isfinite(s))
    count = int(numeric.notna().sum())
    if count <= 1:
        return [0.] * len(values)
    return ((numeric.rank(method='average') - (count + 1) / 2) / (count - 1)).fillna(0.).tolist()


def rank_candidates(rows, histories, calendar, as_of):
    """Rank the entire eligible cross-section using only complete past sessions."""
    days = _calendar(calendar, as_of)
    candidates = []
    seen = set()
    for original in rows:
        if original.get('status', '合格') != '合格':
            continue
        row = deepcopy(original)
        code, peg = row.get('code'), row.get('peg')
        if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code) or code in seen:
            raise ValueError('合格股票代码无效或重复')
        if type(peg) not in (int, float) or not math.isfinite(peg) or peg <= 0:
            raise ValueError('合格股票PEG必须为正有限值')
        if code not in histories:
            raise ValueError(code + ' 缺少完整截面所需历史行情')
        seen.add(code)
        row['factor_values'] = {'peg': float(peg), **_technical_values(_adjusted_close(histories[code], days, as_of))}
        row['factor_scores'] = {}
        row['technical_asof'] = as_of
        candidates.append(row)
    for factor in N2_FACTORS:
        values = [r['factor_values'][factor] for r in candidates]
        if factor == 'peg':
            values = [-v for v in values]
        for row, value in zip(candidates, _centered_rank(values)):
            row['factor_scores'][factor] = value
    for row in candidates:
        row['score'] = sum(row['factor_scores'][factor] * weight for factor, weight in zip(N2_FACTORS, N2_WEIGHTS))
    return sorted(candidates, key=lambda r: (-r['score'], r['peg'], r['code']))


def _capped_weights(raw, total):
    raw = np.asarray(raw, dtype=float)
    result = np.zeros(len(raw))
    active = list(range(len(raw)))
    remaining = total
    while active:
        proposed = remaining * raw[active] / raw[active].sum()
        hit = [i for i, value in zip(active, proposed) if value > N2_SECTOR_CAP]
        if not hit:
            result[active] = proposed
            break
        for i in hit:
            result[i] = N2_SECTOR_CAP
            remaining -= N2_SECTOR_CAP
            active.remove(i)
    return result


def allocation_weights(codes, histories, calendar, as_of, ranks=None):
    """Frozen invvol60 targets; a vacant slot retains its half of capital as cash."""
    days = _calendar(calendar, as_of)
    codes = list(codes)
    if len(codes) > N2_COUNT or len(codes) != len(set(codes)):
        raise ValueError('双股策略目标名额无效')
    ranks = ranks or {}
    codes.sort(key=lambda c: (ranks.get(c, math.inf), c))
    info = dict(allocation='invvol60', stocks=codes, window=0, window_start=None, window_end=None, fallback=None)
    if not codes:
        return {}, info
    raw = np.ones(len(codes))
    if len(codes) > 1:
        info['window'] = 60
        if len(days) < 60:
            info['fallback'] = 'insufficient_calendar'
        else:
            info.update(window_start=days[-60], window_end=days[-1])
            samples = []
            for code in codes:
                if code not in histories:
                    raise ValueError(code + ' 缺少倒波动历史行情')
                sample = _adjusted_close(histories[code], days, as_of).pct_change(fill_method=None).to_numpy()[-60:]
                samples.append(sample)
            sample = np.column_stack(samples)
            if not np.isfinite(sample).all():
                info['fallback'] = 'missing_full_window'
            else:
                covariance = np.atleast_2d(np.cov(sample, rowvar=False, ddof=1))
                if (np.diag(covariance) <= 0).any():
                    info['fallback'] = 'zero_volatility'
                else:
                    raw = 1 / np.sqrt(np.diag(covariance))
    weights = _capped_weights(raw, len(codes) / N2_COUNT)
    return dict(zip(codes, map(float, weights))), info


def initial_state():
    return {'entries': {}, 'pending': {}, 'cooldowns': {}, 'streaks': {}, 'sequence': 0, 'evaluated_day': None}


def within_rebalance_band(current, target, equity, sector_value=None):
    """Amounts share one unit; five percentage points are measured on equity."""
    sector_value = current if sector_value is None else sector_value
    if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0
               for v in (current, target, equity, sector_value)) or equity <= 0:
        raise ValueError('免微调金额或权益无效')
    return abs(current - target) <= N2_BAND * equity and sector_value <= N2_SECTOR_CAP * equity


def _known_sector(value):
    return isinstance(value, str) and value.strip() not in ('', '未知', '待核实')


def select_portfolio(ranked, held, state, day, calendar, sectors=None):
    """Plan intended exits/entries. Actual unfilled exits still occupy live slots."""
    days = _calendar(calendar, day)
    held_codes = set(held)
    updated = {**initial_state(), **deepcopy(state or {})}
    if updated['evaluated_day'] is not None and updated['evaluated_day'] > day:
        raise ValueError('不能把策略状态倒退到较早交易日')
    for key in ('entries', 'pending', 'streaks'):
        updated[key] = {c: v for c, v in updated[key].items() if c in held_codes}
    sector_map = dict(sectors) if sectors is not None else {r['code']: r.get('industry_l1') for r in ranked}
    if sectors is None and isinstance(held, dict):
        for code, position in held.items():
            if code not in sector_map and isinstance(position, dict):
                sector_map[code] = position.get('industry_l1')
    missing = sorted(c for c in held_codes if not _known_sector(sector_map.get(c)))
    if missing:
        raise ValueError('持仓缺少一级行业：' + ', '.join(missing))
    active_cooldowns = {}
    for code, sold in updated['cooldowns'].items():
        _day(sold)
        if sold > day:
            raise ValueError('价格退出冷却日期晚于决策日')
        complete = sum(sold < d < day for d in days)
        if complete < N2_COOLDOWN:
            if days[0] > sold and complete < N2_COOLDOWN and sold not in days:
                raise ValueError('冷却期交易日历覆盖不足')
            active_cooldowns[code] = sold
    updated['cooldowns'] = active_cooldowns
    excluded = set(updated['pending']) | set(active_cooldowns)
    counts = Counter()
    active = []
    seen = set()
    for original in ranked:
        code = original['code']
        if code in seen:
            raise ValueError('排序股票重复')
        seen.add(code)
        sector = sector_map.get(code)
        if code in excluded or not _known_sector(sector) or counts[sector] >= N2_CANDIDATE_SECTOR_COUNT:
            continue
        active.append({**deepcopy(original), 'industry_l1': sector})
        counts[sector] += 1
    ranks = {r['code']: i + 1 for i, r in enumerate(active)}
    exits = {}
    if ranked:
        if updated['evaluated_day'] != day:
            for code in held_codes:
                previous = updated['streaks'].get(code, 0)
                if type(previous) is not int or previous < 0:
                    raise ValueError('离榜确认计数无效')
                updated['streaks'][code] = previous + 1 if ranks.get(code, math.inf) > N2_EXIT_RANK else 0
            updated['evaluated_day'] = day
        exits = {c: 'exit_rank' for c in held_codes if updated['streaks'].get(c, 0) >= N2_CONFIRM}
        overflow = max(0, len(held_codes) - N2_COUNT)
        for code in sorted(held_codes, key=lambda c: (-ranks.get(c, math.inf), c))[:overflow]:
            exits[code] = 'exit_rank'
    exits.update(updated['pending'])
    groups = defaultdict(list)
    for code in held_codes - set(exits):
        groups[sector_map[code]].append(code)
    for members in groups.values():
        for code in sorted(members, key=lambda c: (-ranks.get(c, math.inf), c))[:max(0, len(members) - 1)]:
            exits[code] = 'sector_quota'
    selected = sorted(held_codes - set(exits), key=lambda c: (ranks.get(c, math.inf), c))
    counts = Counter(sector_map[c] for c in selected)
    entrants = []
    for row in active[:N2_EXIT_RANK]:
        if len(selected) >= N2_COUNT:
            break
        code, sector = row['code'], row['industry_l1']
        if code in held_codes or code in exits or counts[sector]:
            continue
        selected.append(code)
        entrants.append(code)
        counts[sector] += 1
    return dict(active_candidates=active[:N2_EXIT_RANK], ranks=ranks, exit_reasons=exits, selected=selected, entrants=entrants), updated


def price_exit_codes(state, histories, as_of):
    """Use the first entry's adjusted anchor; later top-ups never reset it."""
    _day(as_of)
    exits = {}
    for code, entry in state.get('entries', {}).items():
        _day(entry['date'])
        if entry['date'] > as_of:
            continue
        if code not in histories:
            raise ValueError(code + ' 缺少价格退出历史行情')
        raw, adjusted = _history_rows(histories[code], as_of)
        anchor_fen = entry.get('adjusted_anchor_fen')
        if anchor_fen is None:
            factor = entry.get('factor_at_entry')
            if factor is None:
                index = next((i for i, b in enumerate(raw) if b['date'] == entry['date']), None)
                if index is None:
                    raise ValueError(code + ' 缺少原始建仓日复权锚点')
                factor = adjusted[index]['close'] / raw[index]['close']
            if type(factor) not in (int, float) or not math.isfinite(factor) or factor <= 0:
                raise ValueError('原始建仓复权因子无效')
            anchor_fen = entry['price_fen'] * factor
        if type(anchor_fen) not in (int, float) or not math.isfinite(anchor_fen) or anchor_fen <= 0:
            raise ValueError('原始建仓复权锚点无效')
        if adjusted[-1]['close'] * 100 <= anchor_fen * (1 - N2_STOP_LOSS):
            exits[code] = 'price_hard'
    return exits
