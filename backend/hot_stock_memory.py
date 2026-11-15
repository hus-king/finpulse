"""Shared, source-backed project history for hot-news analysis."""
from datetime import date, datetime
import json
from pathlib import Path
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .hot_news_board import digest
from .news_cleaning import SHANGHAI

EPISODES = 'hot_stock_memory_episodes'
PROJECT_EPISODES = 'hot_stock_memory_projects'
MAX_PROJECT_EPISODES = 500
PROJECT_PROGRESS = re.compile(
    r'开工|动工|奠基|建设|施工|延期|延迟|推迟|停工|复工|竣工|投产|产能|停产|扩产|取消|搁置'
    r'|groundbreaking|construction|delay|production|capacity', re.I)


class PublicEpisode(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    source: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    published_at: date
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=4, max_length=12000)


class ProjectParticipant(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: str = Field(pattern=r'^\d{6}$')
    name: str = Field(min_length=2, max_length=60)
    mention: str = Field(min_length=2, max_length=100)
    quote: str = Field(min_length=4, max_length=240)


class ProjectDraft(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    project_id: str = Field(pattern=r'^[a-z0-9-]{3,80}$')
    project_name: str = Field(min_length=6, max_length=100)
    aliases: list[str] = Field(min_length=1, max_length=12)
    event_key: str = Field(pattern=r'^[a-z0-9-]{3,80}$')
    event: str = Field(min_length=2, max_length=100)
    project_quote: str = Field(min_length=4, max_length=240)
    participants: list[ProjectParticipant] = Field(min_length=1, max_length=10)


class ProjectEpisode(ProjectDraft):
    episode: PublicEpisode


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).casefold())


def store_project_episode(store, draft, *, seed=False):
    """Publish one reviewed episode with its linked entities and historical edges."""
    row = ProjectEpisode.model_validate(draft)
    episode = row.episode.model_dump(mode='json')
    text = episode['title'] + '\n' + episode['content']
    aliases = sorted({normalized(row.project_name), *(normalized(alias) for alias in row.aliases)})
    if any(not 6 <= len(alias) <= 100 for alias in aliases):
        raise ValueError('项目别名必须是可区分的完整名称，长度为 6 至 100 字符。')
    if row.project_quote not in text or not any(alias in normalized(row.project_quote) for alias in aliases):
        raise ValueError('项目引文必须来自原文，并包含项目名称或别名。')
    codes = {participant.code for participant in row.participants}
    if len(codes) != len(row.participants):
        raise ValueError('同一事件的参与公司代码重复。')
    for participant in row.participants:
        if participant.quote not in text or normalized(participant.mention) not in normalized(participant.quote):
            raise ValueError('参与方引文必须来自原文，并包含公司名称或别名。')

    episode_id = digest(episode)
    project_id = 'project:' + row.project_id
    event_id = 'event:' + digest([project_id, row.event_key])
    edges = [{'from': event_id, 'to': project_id, 'type': 'concerns_project', 'quote': row.project_quote}]
    companies = []
    for participant in row.participants:
        company_id = 'company:' + participant.code
        companies.append({'id': company_id, 'type': 'company', 'name': participant.name, 'code': participant.code})
        edges.append({'from': event_id, 'to': company_id, 'type': 'project_participant', 'quote': participant.quote})

    key = digest([episode_id, project_id])
    if seed and store.get(PROJECT_EPISODES, key):
        return key
    store.put(PROJECT_EPISODES, key, {
        'episode': {**episode, 'id': episode_id},
        'project': {'id': project_id, 'type': 'project', 'name': row.project_name, 'aliases': aliases},
        'event': {'id': event_id, 'type': 'event', 'name': row.event},
        'companies': companies,
        'edges': edges,
    })
    return key


def store_hot_episode(store, article, sources):
    # The content key ignores heat and crawl time. Preserve the original observation.
    origins = {}
    for source in sources:
        identity = (source['name'], source['url'], source.get('published_at') or '')
        origins[identity] = {
            'name': identity[0], 'url': identity[1], 'published_at': identity[2] or None,
            'observed_at': source.get('observed_at'),
        }
    material = {'title': article['title'], 'content': article['content']}
    key = digest([material, sorted(origins)])
    if store.get(EPISODES, key) is None:
        store.put(EPISODES, key, {**material, 'sources': [origins[item] for item in sorted(origins)]})


def topic_date(topic):
    dates = []
    for source in topic['sources']:
        for field in ('published_at', 'observed_at'):
            try:
                parsed = datetime.fromisoformat(source.get(field) or '')
            except (TypeError, ValueError):
                continue
            dates.append(parsed.astimezone(SHANGHAI).date() if parsed.tzinfo else parsed.date())
            break
    return min(dates).isoformat() if dates else None


def project_evidence(topic, code, memory):
    """Traverse a bounded event → project ← historical event → company path."""
    title = topic['title']
    if not memory or not PROJECT_PROGRESS.search(title):
        return None
    known_by = topic_date(topic)
    if not known_by:
        return None

    title_key = normalized(title)
    matches = [row for row in memory if row['episode']['published_at'] < known_by
               and any(alias in title_key for alias in row['project']['aliases'])]
    if len({row['project']['id'] for row in matches}) != 1:
        return None
    for row in sorted(matches, key=lambda item: (item['episode']['published_at'], item['event']['id']), reverse=True):
        company = next((item for item in row['companies'] if item['code'] == code), None)
        if company is None:
            continue
        episode = row['episode']
        project_edge, *participants = row['edges']
        company_edge = next(edge for edge in participants if edge['to'] == company['id'])
        quotes = list(dict.fromkeys([project_edge['quote'], company_edge['quote']]))
        path = [
            {'from': title, 'relation': '涉及项目', 'to': row['project']['name'], 'quote': title},
            {'from': row['project']['name'], 'relation': '对应历史事件', 'to': row['event']['name'],
             'quote': project_edge['quote'], 'url': episode['url'], 'published_at': episode['published_at']},
            {'from': row['event']['name'], 'relation': '项目参与方', 'to': company['name'],
             'quote': company_edge['quote'], 'url': episode['url'], 'published_at': episode['published_at']},
        ]
        return {
            'path': path,
            'cards': [{'id': digest([episode['id'], quote]), 'text': quote,
                       'url': episode['url'], 'source': episode['source']} for quote in quotes],
        }
    return None


def seed_project_memory(store):
    seed = Path(__file__).parent / 'knowledge' / 'hot_stock_projects.json'
    for row in json.loads(seed.read_text(encoding='utf-8')):
        store_project_episode(store, row, seed=True)
