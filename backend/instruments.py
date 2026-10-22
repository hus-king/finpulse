"""Verified market indices; prefixed identities never alias six-digit equities.

Codes: SSE (sse.com.cn), CSI (csindex.com.cn), SZSE (cnindex.com.cn).
Index volumes retain source units: providers use differing index conventions.
"""
import re

_INDEX_ROWS = [
    ('sh000001', '上证指数', 'SZZS', '上证综指 上海大盘'),
    ('sz399001', '深证成指', 'SZCZ', '深圳成指 深成指'),
    ('sh000300', '沪深300', 'HS300', '沪深三百 CSI300'),
    ('sh000016', '上证50', 'SZ50', '上证五十'),
    ('sh000905', '中证500', 'ZZ500', '中证五百 CSI500'),
    ('sh000852', '中证1000', 'ZZ1000', '中证一千 CSI1000'),
    ('sh000688', '科创50', 'KC50', '科创五十 科创板'),
    ('sz399006', '创业板指', 'CYBZ', '创业板指数 创业指数'),
]
INDICES = {code: {'code': code, 'symbol': code[2:], 'name': name, 'initials': initials,
                  'aliases': aliases, 'exchange': code[:2].upper(), 'asset_type': 'index',
                  'industry': '市场指数', 'price': None, 'change': None}
           for code, name, initials, aliases in _INDEX_ROWS}


def is_index(code):
    return code in INDICES


def supported_code(code):
    return is_index(code) or bool(re.fullmatch(r'\d{6}', code))


def provider_symbol(code):
    if is_index(code):
        return code
    if not re.fullmatch(r'\d{6}', code):
        raise ValueError('Unknown instrument code')
    return ('sh' if code.startswith('6') else 'sz') + code
