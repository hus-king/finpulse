"""Literal evidence coordinates for development-only two-stage weak labels."""
import copy
import re

from .contract import digest, model_state

VERSION = 'news-evidence-v3.1'
SCOPES = {'COMPANY_EVENT', 'INDUSTRY_SIGNAL', 'MACRO_SIGNAL', 'UNKNOWN'}
KINDS = {'PRICE', 'DEMAND', 'SUPPLY', 'REGULATION', 'COMPETITION', 'TECHNOLOGY',
         'FINANCIAL', 'CONTRACT', 'CORPORATE_ACTION', 'OTHER', 'UNKNOWN'}
STAGES = {'PROPOSED', 'ANNOUNCED', 'ACCEPTED', 'APPROVED', 'IMPLEMENTED', 'REPORTED', 'CANCELLED', 'UNKNOWN'}
TRANSMISSIONS = {'DIRECT', 'CONDITIONAL', 'DISTANT', 'SENTIMENT', 'NONE', 'UNKNOWN'}
PRICES = {'INPUT_COST', 'OUTPUT_PRICE', 'BOTH', 'NOT_APPLICABLE', 'UNKNOWN'}


def _packet(identity, visible, prefix, metadata):
    namespace = prefix + '-' + digest({'id': identity, 'visible': visible, 'metadata': metadata})[:24]
    spans, start = [], 0
    for match in re.finditer(r'[^\n。；]+[\n。；]?|[\n。；]', visible):
        for pos in range(match.start(), match.end(), 320):
            end = min(pos + 320, match.end())
            spans.append({'id': namespace + '-' + str(len(spans) + 1), 'start': pos,
                          'end': end, 'text': visible[pos:end]})
            start = end
    if start != len(visible): raise ValueError('Incomplete evidence coverage')
    packet = {'id': identity, 'visible': visible, 'evidence': spans,
              'offset_unit': 'python_unicode_codepoint', **metadata}
    packet['sha256'] = digest(packet)
    return packet


def article_packet(identity, news):
    table = '\n'.join(' | '.join(str(c) for c in row) for row in news.get('market_rows', []))
    return _packet(identity, '\n'.join((news.get('title', ''), news.get('text', ''), table)), 'A', {})


def company_packet(record):
    company = model_state(record)['company']
    visible = '\n'.join(str(x) for k in ('products', 'upstream', 'downstream', 'regions') for x in company[k])
    return _packet(record['id'], visible, 'B', {'company': company})


def _verify(packet):
    base = {k: v for k, v in packet.items() if k != 'sha256'}
    if packet.get('sha256') != digest(base): raise ValueError('Evidence packet digest mismatch')
    pos, ids = 0, set()
    for span in packet['evidence']:
        if (span['id'] in ids or span['start'] != pos or span['end'] <= pos
                or packet['visible'][pos:span['end']] != span['text']):
            raise ValueError('Invalid evidence coordinates')
        ids.add(span['id']); pos = span['end']
    if pos != len(packet['visible']): raise ValueError('Incomplete evidence coverage')


def _strings(value, field):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError('Invalid required array: ' + field)
    if len(set(value)) != len(value): raise ValueError('Duplicate entries: ' + field)
    return value


def resolve(packet, ids):
    _verify(packet)
    _strings(ids, 'evidence_ids')
    byid = {q['id']: q for q in packet['evidence']}
    if any(eid not in byid for eid in ids): raise ValueError('Foreign or unknown evidence ID')
    return [{'id': eid, 'quote': byid[eid]['text'], 'start': byid[eid]['start'],
             'end': byid[eid]['end']} for eid in ids]


def _choice(obj, field, options):
    if not isinstance(obj.get(field), str) or obj[field] not in options: raise ValueError('Invalid ' + field)


def validate_facts(article, answer):
    _verify(article)
    if not isinstance(answer, dict) or answer.get('id') != article['id'] or not isinstance(answer.get('facts'), list):
        raise ValueError('Invalid article envelope')
    seen, facts = set(), []
    for f in answer['facts']:
        if not isinstance(f, dict): raise ValueError('Invalid fact')
        for key in ('fact_id', 'subject_name', 'subject_code', 'description'):
            if not isinstance(f.get(key), str): raise ValueError('Missing fact text: ' + key)
        if not f['fact_id'].strip() or f['fact_id'] in seen or not f['description'].strip(): raise ValueError('Invalid fact identity or description')
        seen.add(f['fact_id'])
        for key, options in [('scope', SCOPES), ('kind', KINDS), ('stage', STAGES)]: _choice(f, key, options)
        quotes = resolve(article, f.get('news_evidence_ids'))
        subjects = resolve(article, f.get('subject_evidence_ids'))
        if not quotes: raise ValueError('Fact requires news evidence')
        if f['scope'] == 'COMPANY_EVENT':
            identifiers = [x for x in (f['subject_name'], f['subject_code']) if x]
            if not identifiers or not subjects: raise ValueError('Company event requires subject evidence')
            for identity in identifiers:
                if identity == f['subject_code']:
                    if not re.fullmatch(r'\d{5,6}|[A-Z][A-Z0-9.-]{0,15}',identity): raise ValueError('Unsupported or incomplete security code')
                    matches = any(re.search(r'(?<![A-Za-z0-9.-])'+re.escape(identity)+r'(?![A-Za-z0-9.-])',q['quote']) for q in subjects)
                else:
                    # Chinese entity boundaries still need semantic review; do not
                    # let an ASCII token such as ABC silently match ABCD.
                    pattern=(r'(?<![A-Za-z0-9])' if re.match(r'[A-Za-z0-9]',identity) else '')+re.escape(identity)
                    if re.search(r'[A-Za-z0-9]$',identity):pattern+=r'(?![A-Za-z0-9])'
                    matches = any(re.search(pattern,q['quote']) for q in subjects)
                if not matches: raise ValueError('Subject identity absent from evidence')
        facts.append({**copy.deepcopy(f), 'resolved_news_evidence': quotes, 'resolved_subject_evidence': subjects})
    signal = ('YES' if any(f['scope'] in {'INDUSTRY_SIGNAL', 'MACRO_SIGNAL'} for f in facts)
              else 'UNKNOWN' if not facts or any(f['scope'] == 'UNKNOWN' for f in facts) else 'NO')
    return {'id': article['id'], 'facts': facts, 'article_signal_presence': signal,
            'article_sha256': article['sha256'], 'human_verified': False}


def validate_pair(article, facts, company, answer):
    _verify(company)
    # Revalidate the dependency instead of trusting saved validation flags.
    checked = validate_facts(article, {'id': facts.get('id'), 'facts': facts.get('facts')})
    if facts.get('article_sha256') != article['sha256']: raise ValueError('Stale article facts')
    if not isinstance(answer, dict) or answer.get('id') != company['id']: raise ValueError('Invalid pair identity')
    _choice(answer, 'own_event', {'YES', 'NO', 'UNKNOWN'})
    if not checked['facts'] and answer['own_event'] != 'UNKNOWN':
        raise ValueError('Empty extraction cannot establish attribution')
    own = _strings(answer.get('own_fact_ids'), 'own_fact_ids')
    byid = {f['fact_id']: f for f in checked['facts']}
    if answer['own_event'] == 'YES':
        if not own: raise ValueError('Own event needs attributed facts')
        for fid in own:
            f = byid.get(fid)
            if not f or f['scope'] != 'COMPANY_EVENT': raise ValueError('Own fact is not a company event')
            target = company['company']
            if f['subject_code'] and f['subject_code'] != target['code']:
                raise ValueError('Conflicting subject security code')
            if f['subject_name'] and f['subject_name'] != target['name']:
                raise ValueError('Subject name requires a verified target alias')
            if not ((f['subject_code'] and f['subject_code'] == target['code'])
                    or (f['subject_name'] and f['subject_name'] == target['name'])):
                raise ValueError('Subject does not match target')
    elif own: raise ValueError('Only YES may carry own_fact_ids')
    links = answer.get('links')
    if not isinstance(links, list) or any(not isinstance(x, dict) for x in links): raise ValueError('Missing links')
    linkids = [x.get('fact_id') for x in links]
    if len(linkids) != len(byid) or set(linkids) != set(byid): raise ValueError('Missing, duplicate or foreign fact links')
    evidence = []
    for link in links:
        _choice(link, 'transmission', TRANSMISSIONS); _choice(link, 'price_role', PRICES)
        conditions = _strings(link.get('missing_conditions'), 'missing_conditions')
        business = resolve(company, link.get('business_evidence_ids')); evidence.extend(business)
        if not isinstance(link.get('mechanism'), str) or not link['mechanism'].strip(): raise ValueError('Missing mechanism')
        if link['transmission'] in {'DIRECT', 'CONDITIONAL', 'DISTANT', 'SENTIMENT'} and not business:
            raise ValueError('Transmission needs business evidence')
        if link['transmission'] in {'CONDITIONAL', 'DISTANT'} and not conditions: raise ValueError('Missing conditions')
        if link['fact_id'] in own and link['transmission'] != 'NONE': raise ValueError('Own event is not external transmission')
        if byid[link['fact_id']]['kind'] != 'PRICE' and link['price_role'] != 'NOT_APPLICABLE': raise ValueError('Non-price fact has price role')
        if link['price_role'] in {'INPUT_COST', 'OUTPUT_PRICE', 'BOTH'} and not business: raise ValueError('Price role needs business evidence')
    return {**copy.deepcopy(answer), 'article_signal_presence': checked['article_signal_presence'],
            'resolved_business_evidence': list({q['id']: q for q in evidence}.values()),
            'human_verified': False, 'semantic_review_required': True, 'label_source': 'AI_weak_reference'}
