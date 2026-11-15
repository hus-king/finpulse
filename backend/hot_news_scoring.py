"""Auditable newsworthiness: model evidence, deterministic repetition and totals."""
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SCORING_VERSION = 'newsworthiness-v1'
RUBRIC_PROMPT = '''每项还必须包含rubric对象，用于100分新闻入榜优先级，不能与个股利好利空分数混用。
只有decision=keep才必须提供完整rubric；review/exclude直接用rubric=null，不为非新闻材料编造事件证据或评分依据。
rubric只含以下字段，各项只能从给定分值选择：identity(0/4/6，主体模糊/可定位/身份明确)，target(0/5，是否明确具体政策产品业务或合作对象)，scope(0/2/4，范围缺失/范围明确/具体规模或名单)，importance(0/5/12/18/25，无实质变化/局部轻微/明确经营变化/重大公司产业品牌安全声誉事件/重大公共系统性事件)，attitude(0/3/5，无态度证据/有归属观点/鲜明态度诉求)，intensity(0/3/5，普通/明显反应/强烈反应)，concentration(0/3/5，个别或未知/多份材料支持一致态度或两方争论/有足够样本支持高度集中或持续对立)，action(0/5，模糊概括/具体动作)，newness(0/3/5，背景或假设/新进展细节少/明确新增事实)，traceability(0/2/5，无材料/平台标题摘要支持/有可追溯报道或公告摘录)，evidence对象包含specificity、importance、opinion、event四个字符串。
四项evidence必须逐字摘录输入title或summary；没有舆论证据时opinion为空且三个舆论项均0，不得从热榜名次推测评论比例。只有平台话题而无报道摘录时traceability最高2。夸张标题、震惊或全网炸锅等词不能单独证明强烈舆论。
品牌产品安全的具体测试质疑、监管行动及重要代言争议引发正式解约，可属于18分重要事件；实际定性、发生范围与来源归属必须按材料表述，不编造缺陷确认。普通合作到期、小事或无具体对象的安全议论不能套用此分值。不得针对特定品牌硬编码分数。重复次数和总分由程序计算，不输出这两项。'''


class RubricEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    specificity: str = Field(max_length=180)
    importance: str = Field(max_length=180)
    opinion: str = Field(max_length=180)
    event: str = Field(max_length=180)


class NewsRubric(BaseModel):
    model_config = ConfigDict(extra='forbid')
    identity: Literal[0, 4, 6]
    target: Literal[0, 5]
    scope: Literal[0, 2, 4]
    importance: Literal[0, 5, 12, 18, 25]
    attitude: Literal[0, 3, 5]
    intensity: Literal[0, 3, 5]
    concentration: Literal[0, 3, 5]
    action: Literal[0, 5]
    newness: Literal[0, 3, 5]
    traceability: Literal[0, 2, 5]
    evidence: RubricEvidence


def validate_rubric(rubric, material, reported=False):
    values = NewsRubric.model_validate(rubric).model_dump()
    dimensions = {'specificity': ('identity', 'target', 'scope'), 'importance': ('importance',),
                  'opinion': ('attitude', 'intensity', 'concentration'), 'event': ('action', 'newness', 'traceability')}
    totals = {'specificity': values['identity'] + values['target'] + values['scope'],
              'importance': values['importance'],
              'opinion': values['attitude'] + values['intensity'] + values['concentration'],
              'event': values['action'] + values['newness'] + values['traceability']}
    for key, score in totals.items():
        quote = values['evidence'][key].strip()
        # Missing support earns zero points; an invented quotation is still rejected.
        if not quote:
            for field in dimensions[key]: values[field] = 0
            continue
        if (score and len(quote) < 2) or (quote and quote not in material):
            raise ValueError(f'Newsworthiness quotation mismatch: {key}, quote={quote!r}')
    if not reported: values['traceability'] = min(values['traceability'], 2)
    return values


def fallback_rubric(topic):
    """Only source-grounded conservative points; never invent opinion or scope."""
    event = topic.get('screening', {}).get('event') or {}
    fact = event.get('new_fact', '')
    actor = event.get('actor', '')
    material = topic['title'] + '\n' + topic.get('summary', '')
    valid = bool(fact and fact in material and actor and actor in material)
    return {'identity': 6 if valid else 0, 'target': 5 if valid and event.get('action') else 0,
            'scope': 0, 'importance': (25 if event.get('importance') == 'major_public' else 18) if valid else 0,
            'attitude': 0, 'intensity': 0, 'concentration': 0,
            'action': 5 if valid else 0, 'newness': 5 if valid else 0, 'traceability': 2 if valid else 0,
            'evidence': {'specificity': actor if valid else '', 'importance': fact if valid else '',
                         'opinion': '', 'event': fact if valid else ''}}


def repetition(topic, now):
    # Fixed ten-minute buckets prevent polling/retries from inflating persistence.
    buckets = defaultdict(dict)
    for sighting in topic.get('_attention_history', []):
        at = sighting.get('at', 0)
        if not 0 <= now - at < 86400: continue
        slot = int(at // 600)
        counts = sighting.get('platform_counts') or {source: 1 for source in sighting.get('platforms', [])}
        for source, count in counts.items():
            buckets[source][slot] = max(buckets[source].get(slot, 0), min(20, max(0, int(count))))
    # Current membership also supports a checkpoint, without pretending it is a new fetch.
    members = topic.get('members') or [topic]
    current = Counter()
    seen = set()
    for member in members:
        for source in member.get('sources', []):
            key = (source['id'], member['id'])
            if source.get('rank') is None or source.get('stale') or key in seen: continue
            seen.add(key)
            at = source.get('observed_timestamp', now)
            if 0 <= now-at < 86400: current[(source['id'], int(at//600))] += 1
    for (source, slot), count in current.items():
        buckets[source][slot] = max(buckets[source].get(slot, 0), count)
    counts = {source: sum(slots.values()) for source, slots in buckets.items()}
    maximum = max(counts.values(), default=0)
    same = 22 if maximum >= 6 else 17 if maximum >= 4 else 12 if maximum >= 3 else 6 if maximum >= 2 else 0
    platforms = len(counts)
    cross = 8 if platforms >= 5 else 7 if platforms == 4 else 5 if platforms == 3 else 3 if platforms == 2 else 0
    return {'same_platform_count': maximum, 'same_platform_score': same,
            'platform_count': platforms, 'cross_platform_score': cross, 'per_platform_counts': counts,
            'window_hours': 24, 'bucket_minutes': 10}


def score_topic(topic, now):
    screening = topic.get('screening') or {}
    rubric = screening.get('rubric')
    method = 'model' if rubric else 'rules'
    rubric = rubric or fallback_rubric(topic)
    specificity = rubric['identity'] + rubric['target'] + rubric['scope']
    opinion = rubric['attitude'] + rubric['intensity'] + rubric['concentration']
    event = rubric['action'] + rubric['newness'] + rubric['traceability']
    detail = repetition(topic, now)
    repeat = detail['same_platform_score'] + detail['cross_platform_score']
    total = specificity + repeat + rubric['importance'] + opinion + event
    eligible = screening.get('decision') == 'keep' and specificity >= 6 and event >= 5
    # The categorical importance rubric has an 18-point major-brand tier.
    supplemental = eligible and specificity >= 12 and event >= 12 and rubric['importance'] >= 18
    decision = ('core' if total >= 70 else 'hot' if total >= 55 or supplemental else
                'candidate' if total >= 40 else 'exclude') if eligible else 'exclude'
    return {'version': SCORING_VERSION, 'specificity_score': specificity, 'repetition_score': repeat,
            'importance_score': rubric['importance'], 'opinion_score': opinion, 'event_score': event,
            'total_score': total, 'decision': decision, 'major_event_supplement': supplemental and total < 55,
            'repetition_detail': detail, 'rubric': rubric, 'method': method,
            'opinion_evidence_status': 'supported' if opinion else 'unknown',
            'note': '新闻关注优先级，不是个股利好利空、涨跌幅或事实核验结论。'}


def attach_scores(topics, now):
    return [{**deepcopy(topic), 'newsworthiness': score_topic(topic, now)} for topic in topics]
