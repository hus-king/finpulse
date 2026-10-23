"""Company-news attention -> day pooling -> cosine-prototype / linear head.

Offline experiment only. No web-service imports should depend on PyTorch.
"""
import math

import torch
from torch import nn


class PrototypeDirectionModel(nn.Module):
    """Text-only candidate: frozen news/company vectors are projected, each
    article attends to the company fact cards, daily summaries are pooled across
    the lookback window, and a small head maps the pooled vector to a logit.

    Two independent design axes are exposed:
      - aggregate: 'attention' learns a weight per day, 'mean' averages days.
      - head: 'cosine' scores cosine similarity to two learned up/down
        prototypes; 'linear' is a plain linear readout.
    """

    def __init__(self, vectors, *, width=32, use_company=True, aggregate='attention', head='cosine'):
        super().__init__()
        if aggregate not in ('attention', 'mean'):
            raise ValueError('aggregate must be attention or mean')
        if head not in ('cosine', 'linear'):
            raise ValueError('head must be cosine or linear')
        self.width = width
        self.use_company = use_company
        self.aggregate = aggregate
        self.head_kind = head
        self.register_buffer('vectors', torch.as_tensor(vectors, dtype=torch.float32))
        self.text_projection = nn.Sequential(nn.Linear(self.vectors.shape[1], width), nn.Tanh())
        self.fact_key = nn.Linear(width, width, bias=False)
        self.fact_value = nn.Linear(width, width, bias=False)
        self.news_query = nn.Linear(width, width, bias=False)
        self.null_fact = nn.Parameter(torch.zeros(width))
        self.null_news = nn.Parameter(torch.zeros(width))
        self.company_gate = nn.Linear(width * 2, width)
        self.article_score = nn.Sequential(nn.Linear(width, 16), nn.Tanh(), nn.Linear(16, 1))
        self.day_score = nn.Linear(width, 1)
        if head == 'cosine':
            self.up = nn.Parameter(torch.randn(width) / math.sqrt(width))
            self.down = nn.Parameter(torch.randn(width) / math.sqrt(width))
            self.temperature = nn.Parameter(torch.tensor(1.0))
        else:
            self.head = nn.Linear(width, 1)

    def forward(self, news_ids, fact_ids, *, explain=False):
        table = self.text_projection(self.vectors)
        events = table[news_ids]  # [B, T, A, w]
        facts = table[fact_ids]   # [B, F, w]
        fact_mask = fact_ids != 0
        if not self.use_company:
            fact_mask = torch.zeros_like(fact_mask)
        facts = torch.cat((facts, self.null_fact.expand(len(facts), 1, -1)), dim=1)
        fact_mask = torch.cat((fact_mask, torch.ones((len(facts), 1), dtype=torch.bool, device=facts.device)), dim=1)
        score = torch.einsum('btad,bfd->btaf', self.news_query(events), self.fact_key(facts)) / math.sqrt(self.width)
        company_weights = score.masked_fill(~fact_mask[:, None, None, :], -torch.inf).softmax(-1)
        context = torch.einsum('btaf,bfd->btad', company_weights, self.fact_value(facts))
        events = events + torch.sigmoid(self.company_gate(torch.cat((events, context), dim=-1))) * context
        events = torch.cat((events, self.null_news.expand(len(events), events.shape[1], 1, -1)), dim=2)
        article_mask = torch.cat((news_ids != 0, torch.ones((*news_ids.shape[:2], 1), dtype=torch.bool, device=news_ids.device)), dim=2)
        news_weights = self.article_score(events).squeeze(-1).masked_fill(~article_mask, -torch.inf).softmax(-1)
        daily = torch.sum(events * news_weights.unsqueeze(-1), dim=2)  # [B, T, w]
        if self.aggregate == 'attention':
            day_weights = self.day_score(daily).squeeze(-1).softmax(-1)  # [B, T]
            summary = torch.sum(daily * day_weights.unsqueeze(-1), dim=1)  # [B, w]
        else:
            summary = daily.mean(dim=1)  # [B, w]
        if self.head_kind == 'cosine':
            up = self.up / self.up.norm()
            down = self.down / self.down.norm()
            s = summary / summary.norm(dim=-1, keepdim=True).clamp_min(1e-8)
            logits = self.temperature * (torch.einsum('bd,d->b', s, up) - torch.einsum('bd,d->b', s, down))
        else:
            logits = self.head(summary).squeeze(-1)
        if explain:
            return logits, {'company': company_weights, 'news': news_weights, 'summary': summary}
        return logits


def zero_shot_logits(table, news_ids, fact_ids, up, down):
    """No-learning baseline: mean-pool frozen vectors, then compare cosine to
    two fixed 'will rise / will fall' prompt embeddings."""
    def pool(ids):
        mask = ids != 0
        gathered = table[ids]  # [B, T, A, dim] or [B, F, dim]
        counts = mask.sum(-1, keepdim=True).clamp_min(1)
        return (gathered * mask.unsqueeze(-1).float()).sum(-2) / counts

    news = pool(news_ids)          # [B, T, dim]
    news_mask = (news_ids != 0).any(-1).float().unsqueeze(-1)  # [B, T, 1]
    news = (news * news_mask).sum(1) / news_mask.sum(1).clamp_min(1)  # [B, dim]
    company = pool(fact_ids)       # [B, F, dim] -> [B, dim]
    s = news + company
    s = s / s.norm(dim=-1, keepdim=True).clamp_min(1e-8)
    up = up / up.norm()
    down = down / down.norm()
    return torch.einsum('bd,d->b', s, up) - torch.einsum('bd,d->b', s, down)
