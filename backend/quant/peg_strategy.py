"""Point-in-time, deterministic low-PEG paper allocation; no forecasting model."""
import math
import re
from copy import deepcopy
from datetime import datetime, time

from ..news_cleaning import SHANGHAI
from ..paper_rules import board_rules, PaperError
from ..peg_snapshot import METHOD_VERSION

PEG_STRATEGY_ID = 'official-low-peg-v1'
PEG_VERSION = 'daily-low-peg-v1'


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:raise ValueError('榜单时间缺少时区')
    return parsed.astimezone(SHANGHAI)


class PegStrategy:
    def __init__(self, snapshot=None, config=None, selected=None):
        self.snapshot = deepcopy(snapshot)
        self.config = self.validate_config(config or {})
        self.selected = deepcopy(selected or [])

    def metadata(self):
        return {'id':PEG_STRATEGY_ID,'name':'低 PEG 每日持仓','version':PEG_VERSION,'available':True,
                'kind':'peg','universe':[r['code'] for r in self.selected],
                'cadence':'交易日10:00后每日一次','config_defaults':{'top_n':5,'group':'all'},
                'description':'按前一晚已发布榜单的正前瞻PEG升序选股，等权目标配置；仅供模拟实验，尚未回测。'}

    def validate_config(self, config):
        if not isinstance(config,dict) or set(config)-{'top_n','group'}:raise ValueError('低PEG策略仅支持持股数量和比较组参数')
        count,group=config.get('top_n',5),config.get('group','all')
        if type(count) is not int or not 1<=count<=5:raise ValueError('持股数量必须是1至5的整数')
        if group not in ('all','金融','非金融'):raise ValueError('比较组只能为全部合格股票、金融或非金融')
        return {'top_n':count,'group':group}

    @staticmethod
    def _valid(snapshot, as_of, cutoff):
        try:
            if not snapshot or not snapshot.get('snapshot_id') or snapshot.get('method_version')!=METHOD_VERSION:return False
            published=timestamp(snapshot['published_at'])
            if published>=cutoff or snapshot.get('market_date')!=as_of:return False
            rows=snapshot['rows']
            if len(rows)!=300 or snapshot['summary']['total']!=300:return False
            codes=[]
            for row in rows:
                if not isinstance(row['code'],str) or not re.fullmatch(r'[036][0-9]{5}',row['code']):return False
                codes.append(row['code'])
                if timestamp(row['collected_at'])>published:return False
                if row.get('status')=='合格':
                    peg=row.get('peg')
                    if type(peg) not in (int,float) or not math.isfinite(peg) or peg<=0 or row.get('group') not in ('金融','非金融'):return False
            return len(set(codes))==300
        except (ValueError,TypeError,KeyError,PaperError):return False

    def prepare(self, records, config, as_of, now):
        normalized=self.validate_config(config)
        cutoff=datetime.combine(now.astimezone(SHANGHAI).date(),time(),SHANGHAI)
        latest=records.get('peg','latest',default=None)
        # One cached read normally; bound fallback history after republication.
        candidates=[latest] if self._valid(latest,as_of,cutoff) else [r['value'] for r in records.list('peg_history',limit=64)]
        candidates=[s for s in candidates if self._valid(s,as_of,cutoff)]
        if not candidates:raise ValueError('缺少上一交易日、今日零点前已发布的有效PEG榜单，保留持仓并等待更新')
        snapshot=max(candidates,key=lambda s:(timestamp(s['published_at']),s['snapshot_id']))
        selected=sorted((r for r in snapshot['rows'] if r['status']=='合格' and
                         (normalized['group']=='all' or r['group']==normalized['group'])),key=lambda r:(r['peg'],r['code']))[:normalized['top_n']]
        if not selected:raise ValueError('当前比较组没有合格PEG样本，保留持仓')
        for row in selected:
            try:board_rules(row['code'])
            except PaperError:raise ValueError(f"{row['name']}（{row['code']}）暂不在模拟盘支持范围，保留持仓并等待人工检查") from None
        return PegStrategy(snapshot,normalized,selected)

    def decide(self, histories, as_of):
        if not self.snapshot or self.snapshot['market_date']!=as_of:raise ValueError('PEG快照尚未绑定信号日')
        for row in self.selected:
            bars=histories.get(row['code'],[])
            if not bars or bars[-1]['date']!=as_of:raise ValueError('PEG目标股票日线未对齐上一完整交易日')
        weight=min(.2,.8/len(self.selected))
        group='全部合格股票' if self.config['group']=='all' else self.config['group']
        return {'as_of':as_of,'target_weights':{r['code']:weight for r in self.selected},
                'scores':{r['code']:r['peg'] for r in self.selected},'model_version':PEG_VERSION,
                'strategy_config':dict(self.config),
                'peg_snapshot':{k:self.snapshot[k] for k in ('snapshot_id','method_version','published_at','market_date')},
                'peg_selection':[{k:r[k] for k in ('code','name','group','peg')} for r in self.selected],
                'reason':f'{group}按正前瞻PEG升序，选择前{len(self.selected)}只；等权目标，实际持仓受成交额、T+1、整手和行情限制。'}
