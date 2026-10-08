"""Explainable news ranking. Priority measures reading value, not expected returns."""
import html
import math
import re
from datetime import datetime

from .prompts import PROMPT_VERSION
from .evidence import score_label
from .catalog import DEFAULT_WATCHLIST, stock_by_code
from .news_cleaning import SHANGHAI, normalize_url, source_page_kind, same_event

TOPICS = {
    '业绩财报': r'业绩|财报|净利|营收|盈利|亏损',
    '公司公告': r'公告|披露|回购|分红|增持|减持|并购|重组',
    '行业政策': r'政策|监管|行业|产业|补贴|关税',
    '风险事件': r'处罚|诉讼|调查|违约|风险|暴雷|退市|减值',
}
VERSION = 'briefing-rank-v4-evidence'


def duplicate_event(left, right):
    def record(row):
        return {'stock': row['code'], 'category': row.get('tag') or 'company_news', 'effective_date': row['time'], 'title': row['title'], 'url': row['url']}
    return left['url'] == right['url'] or same_event(record(left), record(right))[0]


def build_digest(store, owner, now=None):
    now = now or datetime.now(SHANGHAI)
    preferences = store.get('subscription', 'settings', owner, {})
    window = preferences.get('lookback_days', 7)
    maximum = preferences.get('max_items', 10)
    interests = preferences.get('interests', [])
    codes = store.get('watchlist', 'list', owner, DEFAULT_WATCHLIST)
    sections, candidates, warnings = [], [], []
    filtered = 0
    for code in codes:
        snapshot = store.get('dashboard', code, default={})
        stock = store.catalog.get(code) if hasattr(store, 'catalog') else stock_by_code(code)
        stock = stock or snapshot.get('stock') or {'name': code}
        section = {'code': code, 'name': stock['name'], 'as_of': snapshot.get('as_of'), 'news': []}
        sections.append(section)
        if not snapshot.get('as_of'):
            warnings.append(stock['name'] + ' 尚未采集')
        else:
            try:
                age = now - datetime.fromisoformat(snapshot['as_of']).astimezone(SHANGHAI)
                if age.total_seconds() > 86400:
                    warnings.append(stock['name'] + ' 使用超过一天的快照')
            except (ValueError, TypeError):
                warnings.append(stock['name'] + ' 快照时间无法核验')
        warnings.extend((snapshot.get('pipeline') or {}).get('warnings', []))
        for row in snapshot.get('news', []):
            try:
                age = (now.date() - datetime.fromisoformat(row['time']).date()).days
            except (ValueError, TypeError, KeyError):
                continue
            if not 0 <= age <= window or row.get('date_status') in ('missing', 'conflict'):
                continue
            url = normalize_url(row.get('url', ''))
            if not url.startswith(('https://', 'http://')):
                continue
            if source_page_kind(url):
                filtered += 1
                continue
            analysis = row.get('analysis') or {}
            text = row.get('title', '')
            topics = [topic for topic, pattern in TOPICS.items() if re.search(pattern, text)]
            freshness = 30 * math.exp(-age / 3)
            relevance = 25 if row.get('tier', 'company') == 'company' else 12
            magnitude = min(1,abs(row.get('score') or 0) / (100 if analysis.get('version') == PROMPT_VERSION else 2))
            importance = 20 * max(magnitude, .8 if topics else .3)
            evidence = 15 if row.get('text_source') in ('extract', 'extracted', 'extracted_body', 'akshare') else 7
            if row.get('date_status') == 'body_verified':
                evidence = min(20, evidence + 5)
            personalization = 5 + (5 if set(topics) & set(interests) else 0)
            components = {'时效': round(freshness, 1), '关联': relevance, '重要性': round(importance, 1), '证据': evidence, '关注主题': personalization}
            reasons = [f'{age} 天内发布', '关联自选股 ' + stock['name'], '正文已取得' if evidence >= 15 else '仅有片段，证据降权']
            if row.get('news_scope') == 'industry':
                reasons.append('行业间接关联：' + (row.get('relevance_reason') or row.get('industry') or '需核验业务影响'))
            if row.get('refresh_pending'):
                reasons.append('行业检索进行中，暂展示上次材料')
            elif row.get('stale'):
                reasons.append('本轮更新失败，保留旧材料')
            if topics:
                reasons.append('主题：' + '、'.join(topics))
            if set(topics) & set(interests):
                reasons.append('匹配你的关注主题')
            item = {key: row.get(key) for key in ('id', 'title', 'time', 'score', 'source', 'tag', 'text_source')}
            item.update(url=url, score_label=score_label(analysis,row.get('score')), summary=analysis.get('summary'), uncertainty=analysis.get('uncertainty'), code=code, name=stock['name'], topics=topics, priority=round(sum(components.values()) / 105 * 100, 1), components=components, reasons=reasons, related_stocks=[{'code': code, 'name': stock['name']}])
            item.update(news_scope=row.get('news_scope', 'company'), industry=row.get('industry'),
                        related_factors=row.get('related_factors', []), relevance_reason=row.get('relevance_reason'), stale=bool(row.get('stale')),
                        company_impacts=[{'code': code, 'name': stock['name'], 'score': row.get('score'), 'score_label':score_label(analysis,row.get('score')), 'summary': analysis.get('summary'),
                                          'uncertainty': analysis.get('uncertainty'), 'relevance_reason': row.get('relevance_reason'),
                                          'news_scope': row.get('news_scope', 'company'), 'stale': bool(row.get('stale')), 'refresh_pending': bool(row.get('refresh_pending'))}])
            candidates.append(item)
    # Merge near-identical same-day titles/URLs, retaining all associated watchlist stocks.
    unique = []
    for item in sorted(candidates, key=lambda row: (-row['priority'], row['code'], row['id'] or '')):
        duplicate = next((row for row in unique if duplicate_event(row, item)), None)
        if duplicate:
            if item['code'] not in [stock['code'] for stock in duplicate['related_stocks']]:
                duplicate['related_stocks'].extend(item['related_stocks'])
                duplicate['company_impacts'].extend(item['company_impacts'])
                if duplicate['news_scope'] == 'industry' or item['news_scope'] == 'industry':
                    # A shared document is not a shared target-company rating.
                    duplicate['score'] = None
                    duplicate['score_label'] = '查看各公司独立研判'
                    duplicate['summary'] = '同一事件关联多个自选股，影响请查看各公司的独立研判。'
                    duplicate['uncertainty'] = '各公司的业务关联和影响方向需分别核验。'
        else:
            unique.append(item)
    # Greedy diversity penalty stops one company/topic dominating the reading list.
    selected, counts = [], {}
    pending = unique[:]
    while pending and len(selected) < maximum:
        item = max(pending, key=lambda row: row['priority'] - 15 * counts.get(row['code'], 0) - 4 * sum(counts.get(topic, 0) for topic in row['topics']))
        pending.remove(item)
        item['rank'] = len(selected) + 1
        selected.append(item)
        counts[item['code']] = counts.get(item['code'], 0) + 1
        for topic in item['topics']:
            counts[topic] = counts.get(topic, 0) + 1
    for section in sections:
        section['news'] = [row for row in selected if section['code'] in [stock['code'] for stock in row['related_stocks']]]
    digest = {'date': now.date().isoformat(), 'generated_at': now.isoformat(), 'sections': sections, 'recommendations': selected, 'warnings': list(dict.fromkeys(warnings)), 'ranking': {'version': VERSION, 'window_days': window, 'max_items': maximum, 'interests': interests, 'deduplicated': len(candidates) - len(unique), 'filtered_non_news': filtered, 'candidate_count': len(candidates), 'note': '按时效、关联、事件重要性、证据与关注主题排序，并进行公司/主题多样性调整。利好和利空同等参与；优先级不代表预期收益。'}, 'note': '只汇总已保存的真实新闻；缺失和过期快照会提示，优先级不代表买卖建议。'}
    content = '<h1>FinPulse 每日晨报 · ' + digest['date'] + '</h1><p>' + html.escape(digest['note']) + '</p>'
    for item in selected:
        content += '<h2>' + str(item['rank']) + '. <a href="' + html.escape(item['url'], quote=True) + '">' + html.escape(item['title']) + '</a></h2><p>' + html.escape(item['name'] + ' · ' + item['time']) + '</p><p>' + html.escape(item['summary'] or '尚未完成研判，请阅读原文。') + '</p><p>推荐理由：' + html.escape('；'.join(item['reasons'])) + '</p><p>' + html.escape(item['uncertainty'] or '') + '</p>'
        if item['news_scope'] == 'industry' or len(item['company_impacts']) > 1:
            for impact in item['company_impacts']:
                name = impact['name'] + ('（行业检索中，暂用上次材料）' if impact['refresh_pending'] else '（沿用上次材料）' if impact['stale'] else '')
                content += '<p><strong>' + html.escape(name) + '</strong>：' + html.escape(impact['summary'] or '尚未完成该公司研判') + '</p><p>' + html.escape(impact['relevance_reason'] or '') + '</p><p>' + html.escape(impact['uncertainty'] or '') + '</p>'
    if not selected:
        content += '<p>当前时间窗口没有符合条件的新闻；未生成替代内容。</p>'
    if digest['warnings']:
        content += '<h2>数据提示</h2><ul>' + ''.join('<li>' + html.escape(warning) + '</li>' for warning in digest['warnings']) + '</ul>'
    digest['html'] = content
    return digest
