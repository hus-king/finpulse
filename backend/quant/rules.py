"""Bounded JSON arithmetic DSL. No user source code, imports, I/O or eval."""
import hashlib
import json
import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from ..paper_rules import board_rules, PaperError
from .features import FEATURE_NAMES, FEATURE_VERSION, features

MAX_PACKAGE_BYTES=65536
MAX_STRATEGIES=10
MAX_NODES=128
MAX_DEPTH=6
FEATURE_HELP={
    'return_1':'最近1个交易日收益率','return_5':'最近5个交易日收益率',
    'return_10':'最近10个交易日收益率','return_20':'最近20个交易日收益率','return_60':'最近60个交易日收益率',
    'ma_5':'收盘价相对5日均线的偏离','ma_20':'收盘价相对20日均线的偏离','ma_60':'收盘价相对60日均线的偏离',
    'volatility_20':'最近20日收益率标准差','volume_5':'当日成交量相对5日均量的偏离',
    'volume_20':'当日成交量相对20日均量的偏离','range_20':'最近20日平均日振幅（高低差/收盘价）',
}
Finite=Annotated[float,Field(strict=True,allow_inf_nan=False)]
FeatureName=Literal['return_1','return_5','return_10','return_20','return_60','ma_5','ma_20','ma_60','volatility_20','volume_5','volume_20','range_20']
Code=Annotated[str,StringConstraints(strict=True,pattern=r'^[0-9]{6}$')]


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True,allow_inf_nan=False)


class FeatureExpr(StrictModel):
    feature:FeatureName


class ValueExpr(StrictModel):
    value:Annotated[Finite,Field(ge=-1_000_000,le=1_000_000)]


class OperationExpr(StrictModel):
    op:Literal['add','sub','mul','div','min','max','abs']
    args:list['Expression']=Field(min_length=1,max_length=8)

    @model_validator(mode='after')
    def arity(self):
        if self.op=='abs' and len(self.args)!=1 or self.op in ('sub','div') and len(self.args)!=2 or self.op in ('add','mul','min','max') and len(self.args)<2:
            raise ValueError('abs需要1项，sub/div需要2项，其他运算需要2至8项')
        return self


Expression=Union[FeatureExpr,ValueExpr,OperationExpr]
OperationExpr.model_rebuild()


class Filter(StrictModel):
    left:Expression
    op:Literal['gt','gte','lt','lte','eq']
    right:Expression


class Selection(StrictModel):
    top_n:int=Field(default=5,strict=True,ge=1,le=5)
    min_score:Annotated[Finite,Field(ge=-1_000_000,le=1_000_000)] | None=0.0


class Allocation(StrictModel):
    per_stock_weight:Annotated[Finite,Field(ge=0,le=.2)]=.2
    max_total_weight:Annotated[Finite,Field(ge=0,le=.8)]=.8


class RulePackage(StrictModel):
    schema_version:Literal['finpulse.rules.v1']
    name:str=Field(min_length=1,max_length=60,strict=True)
    description:str=Field(default='',max_length=1000,strict=True)
    version:str=Field(min_length=1,max_length=32,pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$',strict=True)
    universe:list[Code]=Field(min_length=1,max_length=30)
    score:Expression
    filters:list[Filter]=Field(default_factory=list,max_length=12)
    selection:Selection=Field(default_factory=Selection)
    allocation:Allocation=Field(default_factory=Allocation)

    @model_validator(mode='before')
    @classmethod
    def bound_expressions(cls,data):
        if not isinstance(data,dict):return data
        count=0
        def walk(node,depth=1):
            nonlocal count
            count+=1
            if depth>MAX_DEPTH or count>MAX_NODES:raise ValueError('表达式最多128个节点，嵌套深度最多6层')
            if isinstance(node,dict) and isinstance(node.get('args'),list):
                if len(node['args'])>8:raise ValueError('每个运算最多8个参数')
                for arg in node['args']:walk(arg,depth+1)
        walk(data.get('score'))
        filters=data.get('filters',[])
        if isinstance(filters,list):
            if len(filters)>12:raise ValueError('最多12个筛选条件')
            for condition in filters:
                if isinstance(condition,dict):walk(condition.get('left'));walk(condition.get('right'))
        return data

    @field_validator('universe')
    @classmethod
    def verified_codes(cls,codes):
        if len(codes)!=len(set(codes)):raise ValueError('股票池不允许重复代码')
        try:
            for code in codes:board_rules(code)
        except PaperError:raise ValueError('只支持模拟盘已支持的沪深A股代码格式') from None
        return codes


def normalize_package(value):
    package=RulePackage.model_validate(value).model_dump(mode='json')
    raw=json.dumps(package,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>MAX_PACKAGE_BYTES:raise ValueError('策略包不能超过64KiB')
    return package,hashlib.sha256(raw).hexdigest()


def evaluate(expression,values):
    if isinstance(expression,FeatureExpr):result=values[expression.feature]
    elif isinstance(expression,ValueExpr):result=expression.value
    else:
        args=[evaluate(arg,values) for arg in expression.args]
        op=expression.op
        if op=='add':result=sum(args)
        elif op=='sub':result=args[0]-args[1]
        elif op=='mul':result=math.prod(args)
        elif op=='div':
            if args[1]==0:raise ValueError('规则计算发生除零，本轮不执行交易')
            result=args[0]/args[1]
        elif op=='min':result=min(args)
        elif op=='max':result=max(args)
        else:result=abs(args[0])
    if not math.isfinite(result):raise ValueError('规则计算出现非有限数，本轮不执行交易')
    return result


class RuleStrategy:
    def __init__(self,package,strategy_id,revision,digest):
        self.package=RulePackage.model_validate(package)
        self.id,self.revision,self.digest=strategy_id,revision,digest
        self.version=f'{self.package.version}:r{revision}:{digest[:12]}'

    def metadata(self):
        p=self.package
        return {'id':self.id,'name':p.name,'description':p.description,'version':self.version,
                'revision':self.revision,'digest':self.digest,'origin':'user','schema_version':p.schema_version,
                'feature_version':FEATURE_VERSION,'universe':p.universe,'cadence':'交易日10:00后每日一次',
                'available':True,'rules_summary':{'top_n':p.selection.top_n,'min_score':p.selection.min_score,
                'per_stock_weight':p.allocation.per_stock_weight,'max_total_weight':p.allocation.max_total_weight}}

    def validate_config(self,config):
        if config:raise ValueError('规则参数在策略包中统一定义')
        return {}

    def decide(self,histories,as_of):
        p=self.package;scores={};accepted=[]
        for code in p.universe:
            bars=histories.get(code,[])
            if not bars or bars[-1]['date']!=as_of:raise ValueError('自定义策略股票池未对齐上一完整交易日')
            values=dict(zip(FEATURE_NAMES,features(bars)))
            score=evaluate(p.score,values);scores[code]=score
            # Evaluate every condition; errors cannot masquerade as normal exclusion.
            checks=[]
            for condition in p.filters:
                left,right=evaluate(condition.left,values),evaluate(condition.right,values)
                checks.append({'gt':left>right,'gte':left>=right,'lt':left<right,'lte':left<=right,'eq':left==right}[condition.op])
            if all(checks) and (p.selection.min_score is None or score>=p.selection.min_score):accepted.append(code)
        selected=sorted(accepted,key=lambda code:(-scores[code],code))[:p.selection.top_n]
        weight=min(p.allocation.per_stock_weight,p.allocation.max_total_weight/len(selected)) if selected else 0
        return {'as_of':as_of,'target_weights':{code:weight for code in selected},'scores':scores,
                'model_version':self.version,'reason':f'我的规则策略：{p.name}；按评分和筛选生成目标组合'}


EXAMPLE={
    'schema_version':'finpulse.rules.v1','name':'我的稳健动量策略','description':'20日与60日动量排序，过滤高波动，仅为规则示例，未做收益验证。','version':'1.0.0',
    'universe':['000001','600519','300750'],
    'score':{'op':'add','args':[{'op':'mul','args':[{'feature':'return_20'},{'value':.6}]},{'op':'mul','args':[{'feature':'return_60'},{'value':.4}]}]},
    'filters':[{'left':{'feature':'volatility_20'},'op':'lt','right':{'value':.05}}],
    'selection':{'top_n':3,'min_score':0.0},'allocation':{'per_stock_weight':.2,'max_total_weight':.6},
}


def submission_schema():
    return {'schema_version':'finpulse.rules.v1','json_schema':RulePackage.model_json_schema(),'example':EXAMPLE,
            'features':FEATURE_HELP,'limits':{'max_package_bytes':MAX_PACKAGE_BYTES,'max_strategies':MAX_STRATEGIES,
            'max_nodes':MAX_NODES,'max_depth':MAX_DEPTH,'writes_per_minute':20},
            'execution':{'cadence':'daily','timezone':'Asia/Shanghai','not_before':'10:00','bars':65},
            'note':'仅解释受限JSON算术规则；不执行Python、JavaScript或任何网络/文件操作。'}
