"""Material-event ranking and a transparent overview, without extra model calls."""
import re
from datetime import datetime

from .news_cleaning import SHANGHAI, metadata_date

HALF_LIFE_DAYS = 7
from .prompts import PROMPT_VERSION


def rank_events(news):
    def priority(row):
        title = row.get('title', '')
        material = bool(re.search(r'业绩|财报|净利|营收|亏损|订单|采购|中标|投产|产能|回购|分红|并购|重组|处罚|诉讼|违约|退市|政策|监管|关税|供需|原油|利率', title))
        routine = bool(re.search(r'盘中|突破年线|资金流|收评|公司高管|董事.*离任', title))
        trusted = row.get('text_source') in ('extracted_body', 'akshare')
        return (3 * material - 3 * routine + trusted + (row.get('date_status') == 'body_verified'), str(row.get('time') or ''))
    return sorted(news, key=priority, reverse=True)


def is_scored(row):
    analysis = row.get('analysis') or {}
    score = analysis.get('sentiment_score')
    return (row.get('analysis_status') == 'completed' and not row.get('refresh_pending')
            and analysis.get('version') in (PROMPT_VERSION,'news-v4-evidence')
            and analysis.get('assessment') in ('positive','negative')
            and isinstance(score,int) and not isinstance(score,bool) and 0 < abs(score) <= 100
            and score % 5 == 0 and row.get('score',score) == score
            and (score > 0) == (analysis['assessment'] == 'positive'))


def build_overview(news, now=None):
    today = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI).date()
    counts = {'positive':0,'negative':0,'pending':0}
    opportunities, risks, watch, series = [], [], [], []
    for row in rank_events(news):
        if not is_scored(row):
            counts['pending'] += 1
            continue
        analysis = row['analysis']
        score = analysis['sentiment_score']
        counts['positive' if score > 0 else 'negative'] += 1
        base = {'news_id':row['id'],'title':row['title'],'url':row.get('url'),'score':score,
                'date':row.get('time'),'confidence':analysis.get('confidence','low'),'horizon':analysis.get('horizon','unclear')}
        series.append(base)
        for field,target in (('positive_factors',opportunities),('negative_factors',risks),('watch_points',watch)):
            for text in analysis.get(field,[]):
                if text not in [item['text'] for item in target]:
                    target.append({**base,'text':text})
    # Stable chronology makes per-event bars readable; materiality still orders evidence lists.
    series.sort(key=lambda row:(row['date'] or '',row['news_id']))
    scores = [row['score'] for row in series]
    dated = []
    for item in series:
        raw_date = item['date']
        published = metadata_date(raw_date) if isinstance(raw_date, str) else None
        age = (today - published).days if published else None
        item.update(time_weight=None, weight_share=None)
        if age is not None and age >= 0:
            item['time_weight'] = 2 ** (-age / HALF_LIFE_DAYS)
            dated.append((item, age))
    net_score = None
    if dated:
        # Scale all weights equally to avoid underflow for an old-only archive.
        youngest = min(age for _, age in dated)
        relative = [(item, 2 ** (-(age - youngest) / HALF_LIFE_DAYS)) for item, age in dated]
        total_weight = sum(weight for _, weight in relative)
        net_score = round(sum(item['score'] * weight for item, weight in relative) / total_weight, 1)
        if net_score == 0:
            net_score = 0.0
        for item, weight in relative:
            item['weight_share'] = round(weight / total_weight * 100, 1)
    return {'status':'positive' if net_score is not None and net_score > 0 else 'negative' if net_score is not None and net_score < 0 else 'pending',
            'counts':counts,'analyzed':len(series),'total':len(news),'net_score':net_score,
            'weighting': {'method':'exponential_decay','half_life_days':HALF_LIFE_DAYS,'as_of':today.isoformat(),
                          'included':len(dated),'excluded_dates':len(series)-len(dated)},
            'score_range':[min(scores),max(scores)] if scores else None,'score_series':series,
            'opportunities':opportunities[:3],'risks':risks[:3],'watch_points':watch[:3],
            'note':'综合分按新闻发布时间加权，权重每 7 个自然日减半；待研判、日期缺失或未来日期不计入。逐条新闻保留原始 AI 分数。AI判断的消息倾向，不是预期涨幅或涨跌概率。'}


def score_label(analysis, score=None):
    value = analysis.get('sentiment_score')
    if analysis.get('version') in (PROMPT_VERSION,'news-v4-evidence'):
        if analysis.get('assessment') in ('positive','negative') and isinstance(value,int) and value != 0:
            return f'{"利好" if value > 0 else "利空"} {value:+d}/100'
        return '待研判'
    return '待研判'
