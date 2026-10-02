"""Initial supported stock universe; no invented market prices."""
CATALOG = [
    {'code': '600519', 'name': '贵州茅台', 'initials': 'GZMT', 'exchange': 'SH', 'industry': '白酒'},
    {'code': '300750', 'name': '宁德时代', 'initials': 'NDSD', 'exchange': 'SZ', 'industry': '新能源'},
    {'code': '688981', 'name': '中芯国际', 'initials': 'ZXGJ', 'exchange': 'SH', 'industry': '半导体'},
    {'code': '000001', 'name': '平安银行', 'initials': 'PAYH', 'exchange': 'SZ', 'industry': '银行'},
    {'code': '002594', 'name': '比亚迪', 'initials': 'BYD', 'exchange': 'SZ', 'industry': '汽车'},
]
DEFAULT_WATCHLIST = ['600519', '300750', '688981']


def stock_by_code(code):
    return next((stock for stock in CATALOG if stock['code'] == code), None)
