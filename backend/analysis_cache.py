"""Durable news judgments, keyed by evidence rather than collection timestamps."""
import hashlib
import json
from copy import deepcopy

from .industry import PROFILE_NOTE, PROFILE_VERSION
from .news_cleaning import normalize_url
from .prompts import EvidenceAnalysis, PROMPT_VERSION, parse_json


def fingerprint(*parts):
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()


def analysis_context(stock, item, profile, business):
    profile = {**profile, 'version': profile.get('version') or PROFILE_VERSION,
               'note': profile.get('note') or PROFILE_NOTE}
    return {'news_scope': item.get('news_scope', 'company'), 'industry': item.get('industry'),
            'related_factors': item.get('related_factors', []), 'relevance_reason': item.get('relevance_reason', ''),
            'industry_profile': {key: profile.get(key) for key in ('industry', 'source', 'version', 'note')},
            'business_profile': {key: business.get(key) for key in
                                 ('main_business', 'revenue_segments', 'source', 'url', 'version', 'status', 'fetched_at', 'note')}}


def stable_context(context):
    result = deepcopy(context)
    # A successful refresh or a temporary fetch failure does not change known
    # business facts. Keep source/version/limitations and all actual facts.
    result['business_profile'] = {key: value for key, value in context['business_profile'].items()
                                  if key not in ('fetched_at', 'status')}
    return result


def legacy_cache_key(stock, item, context, model):
    return fingerprint(stock['code'], item['title'], item['content'][:10000], item.get('time', ''),
                       item.get('date_status', ''), item.get('text_source', ''),
                       json.dumps(context, ensure_ascii=False, sort_keys=True), PROMPT_VERSION, model)


def analysis_cache_key(stock, item, context, model):
    return fingerprint('news-analysis-cache-v2', stock['code'], normalize_url(item['url']),
                       item['title'], item['content'][:10000], item.get('time', ''),
                       item.get('date_status', ''), item.get('text_source', ''),
                       json.dumps(stable_context(context), ensure_ascii=False, sort_keys=True), PROMPT_VERSION, model)


def valid_reply(record):
    if not isinstance(record, dict) or record.get('prompt_version') != PROMPT_VERSION:
        return False
    analysis = record.get('analysis')
    if not isinstance(analysis, dict) or analysis.get('version') != PROMPT_VERSION:
        return False
    if not record.get('model') or not record.get('analyzed_at'):
        return False
    try:
        parse_json(json.dumps({key: value for key, value in analysis.items() if key != 'version'}), EvidenceAnalysis)
    except (ValueError, TypeError):
        return False
    return True


def attach_analysis(item, reply):
    item.update(analysis=deepcopy(reply['analysis']), score=reply['analysis']['sentiment_score'],
                analysis_status='completed', analyzed_at=reply['analyzed_at'], model=reply['model'],
                cached=reply['cached'], analysis_prompt_version=reply['prompt_version'],
                analysis_context_key=reply['context_key'])
    item.pop('analysis_error', None)


class AnalysisCache:
    def __init__(self, store):
        self.store = store

    def save(self, stock, item, context, model, reply):
        key = analysis_cache_key(stock, item, context, model)
        record = {**reply, 'context_key': key, 'cache_version': 2, 'requested_model': model,
                  'stock_code': stock['code'], 'score': reply['analysis']['sentiment_score'],
                  'document': {field: deepcopy(item.get(field)) for field in
                               ('id', 'title', 'url', 'content', 'time', 'text_source', 'date_status', 'sources', 'tag')},
                  'context': stable_context(context)}
        self.store.put('analysis', key, record)
        return record

    def restore(self, stock, items, profile, business, model):
        """Batch lookup every cleaned article, including ones outside model budget.

        Also upgrades identifiable legacy judgments. Never invent a score when
        provenance is missing, the old prompt is incompatible, or facts changed.
        """
        candidates = []
        for item in items:
            if not all(isinstance(item.get(field), str) for field in ('title', 'content', 'url')):
                continue
            context = analysis_context(stock, item, profile, business)
            key = analysis_cache_key(stock, item, context, model)
            legacy = legacy_cache_key(stock, item, context, model)
            candidates.append((item, context, key, legacy))
        records = self.store.get_many('analysis', [key for row in candidates for key in row[2:]])
        restored = 0
        for item, context, key, legacy in candidates:
            record = next((records.get(candidate) for candidate in (key, legacy)
                           if valid_reply(records.get(candidate))), None)
            if record is None and item.get('analysis_context_key') in (key, legacy):
                # Dashboard-only legacy scores are accepted only with a
                # verifiable input key, not just a matching title or URL.
                old = {'analysis': item.get('analysis'), 'prompt_version': item.get('analysis_prompt_version'),
                       'model': item.get('model'), 'analyzed_at': item.get('analyzed_at')}
                if item.get('analysis_status') == 'completed' and valid_reply(old):
                    record = old
            if record is None:
                continue
            if record.get('context_key') != key or record.get('cache_version') != 2:
                record = self.save(stock, item, context, model, record)
                records[key] = record
            attach_analysis(item, {**record, 'cached': True})
            restored += 1
        return restored
