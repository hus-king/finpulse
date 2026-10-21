"""Shared dependency-free, backward-looking features for training and inference."""
import math
import statistics

FEATURE_VERSION = 'ohlcv-v1'
FEATURE_NAMES = ['return_1','return_5','return_10','return_20','return_60',
                 'ma_5','ma_20','ma_60','volatility_20','volume_5','volume_20','range_20']


def features(candles):
    bars = candles[-65:]
    if len(bars) < 65:
        raise ValueError('至少需要65根完整日线')
    if any(a['date'] >= b['date'] for a,b in zip(bars, bars[1:])):
        raise ValueError('日线日期必须严格递增')
    for bar in bars:
        values = [float(bar[k]) for k in ('open','close','high','low','volume')]
        if not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0 or values[4] <= 0:
            raise ValueError('日线数值无效或停牌')
        if values[3] > min(values[:2]) or values[2] < max(values[:2]):
            raise ValueError('日线OHLC无效')
    closes = [float(b['close']) for b in bars]
    volumes = [float(b['volume']) for b in bars]
    result = [closes[-1]/closes[-1-n]-1 for n in (1,5,10,20,60)]
    result += [closes[-1]/statistics.mean(closes[-n:])-1 for n in (5,20,60)]
    result += [statistics.pstdev([closes[i]/closes[i-1]-1 for i in range(len(closes)-20,len(closes))])]
    result += [volumes[-1]/statistics.mean(volumes[-n:])-1 for n in (5,20)]
    result += [statistics.mean((float(b['high'])-float(b['low']))/float(b['close']) for b in bars[-20:])]
    return result


def corporate_action(raw, adjusted):
    """Detect adjustment-factor changes, not ordinary price moves."""
    if len(raw) != len(adjusted) or any(a['date'] != b['date'] for a,b in zip(raw,adjusted)):
        raise ValueError('复权与原始行情日期不一致')
    factors = [float(b['close'])/float(a['close']) for a,b in zip(raw,adjusted)]
    return any(abs(b/a-1) > .002 for a,b in zip(factors,factors[1:]))
