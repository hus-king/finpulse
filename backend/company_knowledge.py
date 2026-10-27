"""Reviewed factual excerpts seed relationship retrieval; never encode impact scores."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from .news_cleaning import SHANGHAI


def curated_relationships(stock, today=None):
    today = today or datetime.now(SHANGHAI).date().isoformat()
    path = Path(__file__).parent / 'knowledge' / 'company_relationships.json'
    data = json.loads(path.read_text())
    if not data['reviewed_at'] <= today <= data['valid_until']: return []
    return [{**row, 'id': hashlib.sha256((row['url'] + row['text']).encode()).hexdigest(),
             'reviewed_at': data['reviewed_at'], 'valid_until': data['valid_until']}
            for row in data['companies'].get(stock['code'], [])]
