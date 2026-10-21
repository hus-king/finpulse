"""Versioned JSON neural network inference; never deserialize executable models."""
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol

from .features import FEATURE_NAMES, FEATURE_VERSION, features

STRATEGY_ID = 'official-multifactor-v1'
MODEL_ROOT = Path(__file__).resolve().parents[2] / 'models' / 'official_quant'


class Strategy(Protocol):
    def metadata(self) -> dict: ...
    def validate_config(self, config: dict) -> dict: ...
    def decide(self, histories: dict, as_of: str) -> dict: ...


class QuantStrategy:
    def __init__(self, root=MODEL_ROOT):
        root = Path(root)
        if (root/'model.json').stat().st_size > 1_000_000:
            raise ValueError('模型过大')
        raw = (root/'model.json').read_bytes()
        self.report = json.loads((root/'report.json').read_text())
        if hashlib.sha256(raw).hexdigest() != self.report['model_sha256']:
            raise ValueError('模型校验和不符')
        self.model = json.loads(raw)
        m = self.model
        if m['schema'] != 1 or m['feature_version'] != FEATURE_VERSION or m['feature_names'] != FEATURE_NAMES:
            raise ValueError('模型特征版本不兼容')
        width = len(FEATURE_NAMES)
        for key in ('mean','scale'):
            if len(m[key]) != width or not all(type(v) in (int,float) and math.isfinite(v) for v in m[key]):
                raise ValueError('模型标准化参数无效')
        if min(m['scale']) <= 0 or not 1 <= len(m['layers']) <= 4:
            raise ValueError('模型维度无效')
        for layer in m['layers']:
            w,b = layer['weight'], layer['bias']
            if not 1 <= len(w) <= 128 or len(w) != len(b) or any(len(row) != width for row in w):
                raise ValueError('模型权重维度无效')
            if not all(type(v) in (int,float) and math.isfinite(v) for v in b + [v for row in w for v in row]):
                raise ValueError('模型权重非有限数')
            width = len(b)
        if width != 1 or not 0 <= m['threshold'] <= .1 or not m['universe'] or len(m['universe']) > 50:
            raise ValueError('模型输出无效')
        if any(not isinstance(c,str) or len(c)!=6 or not c.isdigit() for c in m['universe']):
            raise ValueError('股票池无效')

    def metadata(self):
        return {'id': STRATEGY_ID, 'name':'官方日频多因子 · 研究版', 'version':self.model['version'],
                'feature_version':FEATURE_VERSION, 'universe':self.model['universe'],
                'cadence':'交易日10:00后每日一次', 'available':True, 'report':self.report}

    def validate_config(self, config):
        if config:
            raise ValueError('此官方策略使用固定风控配置')
        return {}

    def predict(self, vector):
        if len(vector) != len(FEATURE_NAMES) or not all(math.isfinite(v) for v in vector):
            raise ValueError('预测特征无效')
        x = [max(-8,min(8,(v-m)/s)) for v,m,s in zip(vector,self.model['mean'],self.model['scale'])]
        for index,layer in enumerate(self.model['layers']):
            x = [sum(a*b for a,b in zip(row,x))+bias for row,bias in zip(layer['weight'],layer['bias'])]
            if index < len(self.model['layers'])-1:
                x = [max(0,v) for v in x]
        if not math.isfinite(x[0]):
            raise ValueError('预测数值无效')
        return x[0]

    def decide(self, histories, as_of):
        scores = {}
        for code in self.model['universe']:
            bars = histories.get(code, [])
            if not bars or bars[-1]['date'] != as_of:
                raise ValueError('股票池日线未对齐上一完整交易日')
            scores[code] = self.predict(features(bars))
        selected = sorted((c for c in scores if scores[c] > self.model['threshold']), key=lambda c:(-scores[c],c))[:5]
        weight = min(.2,.8/len(selected)) if selected else 0
        return {'as_of':as_of, 'target_weights':{c:weight for c in selected}, 'scores':scores,
                'model_version':self.model['version'], 'reason':'按未来5日预测收益排序，低于阈值保留现金'}


class StrategyRegistry:
    def __init__(self):
        self.strategies = {}
        self.error = None
        try:
            self.register(STRATEGY_ID, QuantStrategy())
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = '官方模型尚未发布或未通过校验'

    def register(self, strategy_id, strategy: Strategy):
        if strategy_id in self.strategies:
            raise ValueError('策略ID已注册')
        self.strategies[strategy_id] = strategy

    def get(self, strategy_id):
        if strategy_id not in self.strategies:
            raise ValueError('该策略当前不可用')
        return self.strategies[strategy_id]

    def list(self):
        return [s.metadata() for s in self.strategies.values()] or [{'id':STRATEGY_ID,'name':'官方日频多因子 · 研究版', 'available':False,'reason':self.error}]
