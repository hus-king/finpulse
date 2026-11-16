"""Group concrete event coverage and measure observed attention, never sentiment."""
from copy import deepcopy
import re
import unicodedata

GROUPING_VERSION = 'bigram-jaccard-200-v1'
GROUP_TEXT_LIMIT = 200
GROUP_MIN_SIMILARITY = 0.40


def normalized_title(title):
    value = unicodedata.normalize('NFKC', title).casefold()
    return re.sub(r'[\W_]+', '', value)


def bigrams(text):
    normalized = normalized_title(text[:GROUP_TEXT_LIMIT])
    return {normalized[index:index + 2] for index in range(len(normalized) - 1)}


def jaccard(left, right):
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def cluster_events(topics):
    """Group similar reports only when every pair in the group meets the threshold."""
    groups = []
    for topic in topics:
        title = topic['title']
        profile = (bigrams(title), bigrams(title + '\n' + topic.get('summary', '')))
        member = (topic['id'], profile)
        for group in groups:
            if all(max(jaccard(profile[0], candidate[0]), jaccard(profile[1], candidate[1]))
                   >= GROUP_MIN_SIMILARITY for _, candidate in group):
                group.append(member)
                break
        else:
            groups.append([member])
    return group_reports(topics, [{'topic_ids': [topic_id for topic_id, _ in group]} for group in groups])


def group_reports(topics, groups):
    by_id = {topic['id']: topic for topic in topics}
    result = []
    for group in groups:
        members = [by_id[key] for key in group['topic_ids']]
        first = min(members, key=lambda topic: (-topic.get('rank_score', 0), len(topic['title'])))
        row = deepcopy(first)
        row['members'] = [{'id': item['id'], 'title': item['title'], 'sources': deepcopy(item['sources'])} for item in members]
        if len(members) > 1:
            # Keep the leading, source-grounded headline, not an unchecked generated headline.
            row['summary'] = '\n\n'.join(dict.fromkeys(item['title'] + '\n' + item.get('summary', '') for item in members))[:5000]
            body = '\n\n'.join(dict.fromkeys(item['title'] + '\n' + (item.get('_content') or item.get('summary', '')) for item in members))
            row['_content'] = body[:12000]
            row['_content_truncated'] = len(body) > 12000 or any(item.get('_content_truncated') for item in members)
            sources = {}
            for item in members:
                for source in item['sources']:
                    prior = sources.get(source['id'])
                    if prior is None or (source.get('rank') or 999) < (prior.get('rank') or 999): sources[source['id']] = deepcopy(source)
            row['sources'] = list(sources.values())
            # All member sector hypotheses remain independently conditional.
            sectors = {}
            for item in members:
                for sector in item.get('screening', {}).get('sectors', []): sectors.setdefault(sector['id'], sector)
            if 'screening' in row: row['screening']['sectors'] = list(sectors.values())[:3]
        result.append(row)
    return result


def attach_attention(topics, previous, now):
    result = []
    for topic in topics:
        row = deepcopy(topic)
        members = row.get('members') or [{'id': row['id'], 'title': row['title'], 'sources': row['sources']}]
        member_ids = {member['id'] for member in members}
        seen = {}
        for old in previous:
            old_members = {member['id'] for member in old.get('members', [])} or {old['id']}
            if member_ids & old_members:
                for sighting in old.get('_attention_history', []):
                    if 0 <= now - sighting['at'] < 86400: seen[sighting['at']] = sighting
        appearances = {(source['id'], member['id']): source for member in members for source in member['sources']
                       if source.get('rank') is not None and not source.get('stale')}
        platforms = sorted({source for source, _ in appearances})
        if appearances: seen[now] = {'at': now, 'platforms': platforms,
                                     'platform_counts': {source: sum(platform == source for platform, _ in appearances) for source in platforms}}
        history = sorted(seen.values(), key=lambda sighting: sighting['at'])[-144:]
        ranks = [source['rank'] for source in appearances.values()]
        snapshots = len(history)
        priority = len(appearances) >= 3 or len(platforms) >= 2 or snapshots >= 3
        row['_attention_history'] = history
        row['attention'] = {'mentions': len(appearances), 'platforms': len(platforms), 'snapshots': snapshots,
                            'best_rank': min(ranks) if ranks else None, 'priority': priority,
                            'related_reports': len(members),
                            'score': round(sum(1 / (60 + rank) for rank in ranks) + .008 * min(max(snapshots - 1, 0), 12), 8)}
        result.append(row)
    return result
