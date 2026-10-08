"""Material-event ranking and a transparent overview, without extra model calls."""
import re
from .prompts import PROMPT_VERSION


def rank_events(news):
    def priority(row):
        title = row.get('title', '')
        material = bool(re.search(r'业绩|财报|净利|营收|亏损|订单|采购|中标|投产|产能|回购|分红|并购|重组|处罚|诉讼|违约|退市|政策|监管|关税|供需|原油|利率', title))
        routine = bool(re.search(r'盘中|突破年线|资金流|收评|公司高管|董事.*离任', title))
        trusted = row.get('text_source') in ('extracted_body', 'akshare')
        return (3 * material - 3 * routine + trusted + (row.get('date_status') == 'body_verified'), row.get('time', ''))
    return sorted(news, key=priority, reverse=True)


def build_overview(news):
    counts = dict.fromkeys(('positive', 'negative', 'neutral', 'mixed', 'insufficient', 'legacy', 'pending'), 0)
    opportunities, risks, watch, directional = [], [], [], []
    for row in rank_events(news):
        analysis = row.get('analysis') or {}
        if row.get('analysis_status') != 'completed':
            counts['pending'] += 1
            continue
        if analysis.get('version') != PROMPT_VERSION:
            counts['legacy'] += 1
            continue
        state = analysis['assessment']
        counts[state] += 1
        base = {'news_id': row['id'], 'title': row['title'], 'url': row.get('url'), 'score': analysis['sentiment_score'], 'confidence': analysis['confidence'], 'horizon': analysis['horizon']}
        for field, target in (('positive_factors', opportunities), ('negative_factors', risks), ('watch_points', watch)):
            for text in analysis[field]:
                if text not in [item['text'] for item in target]:
                    target.append({**base, 'text': text})
        if analysis['sentiment_score'] is not None:
            directional.append(analysis['sentiment_score'])
    if counts['mixed'] or counts['positive'] and counts['negative']:
        status = 'mixed'
    elif counts['positive']:
        status = 'positive'
    elif counts['negative']:
        status = 'negative'
    elif counts['neutral']:
        status = 'neutral'
    else:
        status = 'insufficient'
    return {'status': status, 'counts': counts, 'analyzed': sum(counts[k] for k in ('positive','negative','neutral','mixed','insufficient')), 'total':len(news),
            'score_range': [min(directional), max(directional)] if directional else None,
            'opportunities': opportunities[:3], 'risks': risks[:3], 'watch_points': watch[:3],
            'note': '按独立事件展示证据；未研判与证据不足不作中性，正负影响不做简单平均。分数不是收益率或涨跌概率。'}


def score_label(analysis, score=None):
    if analysis.get('version') == PROMPT_VERSION:
        if analysis.get('assessment') in ('insufficient','mixed'):
            return '待补证' if analysis['assessment'] == 'insufficient' else '正负影响并存'
        value = analysis.get('sentiment_score')
        return f'影响 {value:+d}/100' if value is not None else '待补证'
    return f'旧版 {score:+d}/2' if isinstance(score,int) else '未研判'
