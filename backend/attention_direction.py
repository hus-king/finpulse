"""Small frozen-vector company/news attention + GRU + temporal attention model.

Offline experiment only. No web-service imports should depend on PyTorch.
"""
import math

import torch
from torch import nn


class AttentionDirectionModel(nn.Module):
    def __init__(self, vectors, market_mean, market_scale, day_mean, day_scale, *, use_company=True, width=32,
                 news_time_width=2, recency_half_life=None):
        super().__init__()
        self.use_company = use_company
        self.width = width
        if recency_half_life is not None and (news_time_width != 8 or recency_half_life <= 0):
            raise ValueError('Recency requires session features and a positive trading-day half-life')
        self.recency_half_life = recency_half_life
        self.register_buffer('vectors', torch.as_tensor(vectors, dtype=torch.float32))
        for name, value in [('market_mean', market_mean), ('market_scale', market_scale),
                            ('day_mean', day_mean), ('day_scale', day_scale)]:
            self.register_buffer(name, torch.as_tensor(value, dtype=torch.float32))
        self.text_projection = nn.Sequential(nn.Linear(self.vectors.shape[1], width), nn.Tanh())
        self.news_time_projection = nn.Linear(news_time_width, width, bias=False)
        self.fact_key = nn.Linear(width, width, bias=False)
        self.fact_value = nn.Linear(width, width, bias=False)
        self.news_query = nn.Linear(width, width, bias=False)
        self.null_fact = nn.Parameter(torch.zeros(width))
        self.null_news = nn.Parameter(torch.zeros(width))
        self.company_gate = nn.Linear(width * 2, width)
        self.article_score = nn.Sequential(nn.Linear(width, 16), nn.Tanh(), nn.Linear(16, 1))
        self.gru = nn.GRU(width + 6 + 4, width, batch_first=True)
        self.temporal_state = nn.Linear(width, width, bias=False)
        self.temporal_query = nn.Linear(width, width, bias=False)
        self.temporal_time = nn.Linear(4, width, bias=False)
        self.temporal_score = nn.Linear(width, 1)
        self.head = nn.Sequential(nn.Linear(width * 2 + 10 + 1, 16), nn.ReLU(), nn.Dropout(.2), nn.Linear(16, 1))

    def forward(self, news_ids, news_time, fact_ids, day_market, day_time, market, *, explain=False):
        table = self.text_projection(self.vectors)
        events = table[news_ids] + self.news_time_projection(news_time)
        facts = table[fact_ids]
        fact_mask = fact_ids != 0
        if not self.use_company:
            fact_mask = torch.zeros_like(fact_mask)
        # A real null alternative avoids all-masked softmax and forced relevance.
        facts = torch.cat((facts, self.null_fact.expand(len(facts), 1, -1)), dim=1)
        fact_mask = torch.cat((fact_mask, torch.ones((len(facts), 1), dtype=torch.bool, device=facts.device)), dim=1)
        score = torch.einsum('btad,bfd->btaf', self.news_query(events), self.fact_key(facts)) / math.sqrt(self.width)
        company_weights = score.masked_fill(~fact_mask[:, None, None, :], -torch.inf).softmax(-1)
        context = torch.einsum('btaf,bfd->btad', company_weights, self.fact_value(facts))
        events = events + torch.sigmoid(self.company_gate(torch.cat((events, context), dim=-1))) * context
        events = torch.cat((events, self.null_news.expand(len(events), events.shape[1], 1, -1)), dim=2)
        article_mask = torch.cat((news_ids != 0, torch.ones((*news_ids.shape[:2], 1), dtype=torch.bool, device=news_ids.device)), dim=2)
        news_weights = self.article_score(events).squeeze(-1).masked_fill(~article_mask, -torch.inf).softmax(-1)
        decay = torch.ones_like(news_weights)
        if self.recency_half_life is not None:
            # Apply outside softmax: even a day's sole article loses amplitude
            # as it ages. Publication age, not late ingestion, sets freshness.
            decay = torch.cat((torch.exp(-math.log(2) * news_time[..., 7] / self.recency_half_life),
                               torch.ones_like(news_weights[..., -1:])), dim=-1)
        daily = torch.sum(events * (news_weights * decay).unsqueeze(-1), dim=2)
        day_market = (day_market - self.day_mean) / self.day_scale
        states, _ = self.gru(torch.cat((daily, day_market, day_time), dim=-1))
        last = states[:, -1]
        scores = self.temporal_score(torch.tanh(self.temporal_state(states) + self.temporal_query(last)[:, None, :] + self.temporal_time(day_time))).squeeze(-1)
        if self.recency_half_life is not None:
            # This prior is on state readout, not a claim of causal attribution.
            scores = scores - math.log(2) * day_time[..., 0] * 9 / self.recency_half_life
        temporal_weights = scores.softmax(-1)
        summary = torch.sum(states * temporal_weights.unsqueeze(-1), dim=1)
        company_present = fact_mask[:, :-1].any(-1).float().unsqueeze(-1)
        market = (market - self.market_mean) / self.market_scale
        logits = self.head(torch.cat((summary, last, market, company_present), dim=-1)).squeeze(-1)
        if explain:
            return logits, {'company': company_weights, 'news': news_weights, 'time': temporal_weights,
                            'news_decay': decay, 'news_effective_weight': news_weights * decay}
        return logits
