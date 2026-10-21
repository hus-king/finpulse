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
    """Tencent adjustments are piecewise AFFINE (cash dividends + splits).

    Ratio changes alone falsely flag every cash-adjusted price move. Fit OHLC
    to one affine map per window; a real ex-action changes slope/intercept.
    Two-fen residual tolerance allows upstream cent rounding.
    """
    if len(raw) != len(adjusted) or not raw or any(a['date'] != b['date'] for a,b in zip(raw,adjusted)):
        raise ValueError('复权与原始行情日期不一致')
    pairs=[(float(a[k]),float(b[k])) for a,b in zip(raw,adjusted) for k in ('open','close','high','low')]
    if not all(math.isfinite(x) and math.isfinite(y) and x>0 for x,y in pairs):
        raise ValueError('复权参照数值无效')
    mx=sum(x for x,y in pairs)/len(pairs);my=sum(y for x,y in pairs)/len(pairs)
    variance=sum((x-mx)**2 for x,y in pairs)
    if variance<1e-12:
        return max(y for x,y in pairs)-min(y for x,y in pairs)>.025
    slope=sum((x-mx)*(y-my) for x,y in pairs)/variance
    tolerance=.025+max(abs(y) for x,y in pairs)*.000005
    return slope<=0 or max(abs(y-(my+slope*(x-mx))) for x,y in pairs)>tolerance
