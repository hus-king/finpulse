"""Shanghai-time auction sessions with an explicitly verified holiday year.

Source: https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml
Unknown years are reported as unknown rather than inferred from weekdays.
"""
from datetime import datetime, date, time, timedelta

from .news_cleaning import SHANGHAI

CALENDAR_URL = 'https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20251222_10802510.shtml'
HOLIDAYS = {
    2026: [('01-01', '01-03'), ('02-15', '02-23'), ('04-04', '04-06'),
           ('05-01', '05-05'), ('06-19', '06-21'), ('09-25', '09-27'), ('10-01', '10-07')],
}


def is_trade_day(day):
    if day.year not in HOLIDAYS:
        return None
    return day.weekday() < 5 and not any(start <= day.strftime('%m-%d') <= end for start, end in HOLIDAYS[day.year])


def previous_trade_day(day, include=False):
    for offset in range(0 if include else 1, 32):
        candidate = day - timedelta(days=offset)
        trading = is_trade_day(candidate)
        if trading is None:
            return None
        if trading:
            return candidate
    return None


def market_state(now=None):
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    day, clock = now.date(), now.time().replace(tzinfo=None)
    trading = is_trade_day(day)
    expected, final_session = None, None
    if trading is None:
        state, label = 'calendar_unknown', '交易日历待更新'
    elif not trading:
        state, label = 'closed', '周末休市' if day.weekday() >= 5 else '节假日休市'
        previous = previous_trade_day(day)
        expected = datetime.combine(previous, time(15), SHANGHAI) if previous else None
    elif clock < time(9, 30):
        state, label = 'pre_open', '尚未开盘'
        previous = previous_trade_day(day)
        expected = datetime.combine(previous, time(15), SHANGHAI) if previous else None
    elif clock < time(11, 30):
        state, label = 'trading', '交易中'
        expected = now - timedelta(minutes=2)
    elif clock < time(13):
        state, label = 'lunch_break', '午间休市'
        expected = datetime.combine(day, time(11, 30), SHANGHAI)
        if time(11, 30, 30) <= clock < time(11, 35):
            final_session = day.isoformat() + ':morning'
    elif clock < time(15):
        state, label = 'trading', '收盘集合竞价' if clock >= time(14, 57) else '交易中'
        expected = now - timedelta(minutes=2)
    else:
        state, label = 'after_close', '已收盘'
        expected = datetime.combine(day, time(15), SHANGHAI)
        if time(15, 0, 30) <= clock < time(15, 5):
            final_session = day.isoformat() + ':afternoon'
    return {'state': state, 'label': label, 'is_trading': state == 'trading',
            'is_trade_day': trading, 'server_time': now.isoformat(), 'timezone': 'Asia/Shanghai',
            'calendar_year': day.year if trading is not None else None, 'calendar_source': CALENDAR_URL,
            'expected_data_time': expected.isoformat() if expected else None,
            'final_session': final_session}
