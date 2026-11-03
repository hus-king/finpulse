"""Calendar and cached benchmark for retrospective news replay; no model work."""
from datetime import datetime, timedelta

from .news_cleaning import SHANGHAI
from .trading_calendar import is_trade_day, market_state, previous_trade_day


def replay_market_context(record, request_state, now=None):
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    state = market_state(now)
    completed = (previous_trade_day(now.date(), include=state['state'] == 'after_close')
                 if state['is_trade_day'] is not None else None)
    start = now.date() - timedelta(days=365)
    # Include future sessions to describe an unfinished 5-day observation.
    days = [start + timedelta(days=offset) for offset in range(387)]
    return {
        'server_time': now.isoformat(),
        'completed_through': completed.isoformat() if completed else None,
        'calendar': [{'date': day.isoformat(), 'is_trading': is_trade_day(day)} for day in days],
        'benchmark': {'candles': record.get('candles', []),
                      'refreshing': request_state['refreshing'], 'error': request_state.get('error')},
    }
