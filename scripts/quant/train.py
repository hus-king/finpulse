"""Date-purged GPU MLP training, frozen test, conservative paper-price backtest."""
import argparse
import copy
import hashlib
import json
import math
import os
import statistics
import time
from pathlib import Path

from backend.quant.features import FEATURE_NAMES, FEATURE_VERSION, features, corporate_action
from backend.quant.strategy import QuantStrategy
from backend.paper_rules import fees


def make_samples(data):
    rows=[]
    for code,item in data.items():
        raw,adj=item['raw'],item['adjusted']
        if [r['date'] for r in raw]!=[r['date'] for r in adj]: raise ValueError('数据日期不一致')
        for i in range(64,len(raw)-6):
            try:
                if corporate_action(raw[i-64:i+7],adj[i-64:i+7]): continue
                vector=features(raw[:i+1])
                y=raw[i+6]['open']/raw[i+1]['open']-1
                if abs(y)>.5: continue
                rows.append({'code':code,'date':raw[i]['date'],'label_end':raw[i+6]['date'],'x':vector,'y':y})
            except (ValueError,ZeroDivisionError): continue
    return sorted(rows,key=lambda r:(r['date'],r['code']))


def split_samples(rows):
    dates=sorted(set(r['date'] for r in rows))
    first,second=dates[int(len(dates)*.6)],dates[int(len(dates)*.8)]
    return ([r for r in rows if r['date']<first and r['label_end']<first],
            [r for r in rows if first<=r['date']<second and r['label_end']<second],
            [r for r in rows if r['date']>=second])


def metrics(equity, years=None, trades=0):
    total=equity[-1]/equity[0]-1
    returns=[b/a-1 for a,b in zip(equity,equity[1:])]
    peak=equity[0]; dd=0
    for value in equity:
        peak=max(peak,value); dd=max(dd,1-value/peak)
    years=years or max(1,len(returns))/252
    return {'total_return':total,'annualized_return':(1+total)**(1/years)-1,
            'max_drawdown':dd,'sharpe':statistics.mean(returns)/statistics.pstdev(returns)*math.sqrt(252) if len(returns)>1 and statistics.pstdev(returns)>0 else 0,
            'trades':trades,'positive_days':sum(v>0 for v in returns),'days':len(returns)}


def backtest(data, strategy, dates, kind='model'):
    # No future eligibility filters: current signal uses only previous completed bars.
    lookup={c:{r['date']:i for i,r in enumerate(d['raw'])} for c,d in data.items()}
    cash=20_000_000; positions={}; curve=[cash]; fills=[]; events=0; total_gross=0
    days=sorted(set(dates)); suspended=False; highwater=cash
    for signal in days:
        idx={c:lookup[c].get(signal) for c in data}
        # Skip days not jointly covered, never fill a missing day with a future bar.
        if any(i is None or i+1>=len(data[c]['raw']) for c,i in idx.items()): continue
        next_days={data[c]['raw'][i+1]['date'] for c,i in idx.items()}
        if len(next_days)!=1: continue
        day=next(iter(next_days)); prices={c:round(data[c]['raw'][i+1]['open']*100) for c,i in idx.items()}
        equity=cash+sum(p['quantity']*prices[c] for c,p in positions.items()); highwater=max(highwater,equity)
        if equity<highwater*.85: suspended=True
        histories={c:d['raw'][:idx[c]+1] for c,d in data.items()}
        try:
            if kind=='model': weights=strategy.decide(histories,signal)['target_weights']
            elif kind=='momentum':
                selected=sorted(data,key=lambda c:(-features(histories[c])[3],c))[:5]
                weights={c:.16 for c in selected if features(histories[c])[3]>0}
            else: weights={c:.8/len(data) for c in data}
        except ValueError: weights={}
        # Only past-known corporate action history controls today's new risk.
        eligible={c:not corporate_action(data[c]['raw'][idx[c]-64:idx[c]+1],data[c]['adjusted'][idx[c]-64:idx[c]+1]) for c in data}
        budget=int(equity*.3); spent=0
        targets={c:int(equity*weights.get(c,0)/prices[c])//100*100 for c in data}
        for side in ('sell','buy'):
            for c in sorted(data):
                if suspended: continue
                p=positions.get(c,{'quantity':0,'cost':0,'day':''})
                diff=targets[c]-p['quantity']
                quantity=(-diff if side=='sell' else diff)//100*100
                if quantity<=0 or side=='buy' and not eligible[c] or side=='sell' and p['day']>=day: continue
                price=round(prices[c]*(1.001 if side=='buy' else .999))
                quantity=min(quantity,((budget-spent)//price)//100*100)
                if side=='buy':
                    quantity=min(quantity,max(0,(cash-1000)//price)//100*100)
                    market_total=sum(v['quantity']*prices[k] for k,v in positions.items())
                    quantity=min(quantity,max(0,int(equity*.8)-market_total)//price//100*100,
                                 max(0,int(equity*.2)-p['quantity']*prices[c])//price//100*100)
                if quantity<=0: continue
                gross=price*quantity; fee=fees(gross,side)['fees_fen']
                if side=='buy':
                    cash-=gross+fee; p={'quantity':p['quantity']+quantity,'cost':p['cost']+gross+fee,'day':day}; positions[c]=p
                else:
                    cost=p['cost']*quantity/p['quantity']; cash+=gross-fee
                    fills.append(gross-fee-cost)
                    p={'quantity':p['quantity']-quantity,'cost':p['cost']-cost,'day':p['day']}
                    if p['quantity']: positions[c]=p
                    else: positions.pop(c,None)
                total_gross+=gross; spent+=gross
        for c,p in positions.items():
            i=idx[c]
            if corporate_action(data[c]['raw'][i:i+2],data[c]['adjusted'][i:i+2]): events+=1
        close={c:round(data[c]['raw'][i+1]['close']*100) for c,i in idx.items()}
        curve.append(cash+sum(p['quantity']*close[c] for c,p in positions.items()))
    result=metrics(curve,trades=len(fills))
    result.update(win_rate=sum(p>0 for p in fills)/len(fills) if fills else None,
                  turnover=total_gross/(statistics.mean(curve) or 1),corporate_action_holding_days=events,
                  risk_paused=suspended, start=days[0],end=days[-1], equity_curve=curve)
    return result


def train(root, output, device='cuda'):
    import numpy as np
    import torch
    from torch import nn
    torch.set_num_threads(1); torch.set_num_interop_threads(1); torch.manual_seed(194)
    if device=='cuda':
        if not torch.cuda.is_available(): raise RuntimeError('GPU不可用')
        free,total=torch.cuda.mem_get_info()
        if free<6*1024**3: raise RuntimeError('GPU剩余显存不足6GiB')
        torch.cuda.set_per_process_memory_fraction(min(.08,2*1024**3/total))
    root,output=Path(root),Path(output); output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest['errors']: raise ValueError('数据采集未完整')
    data={}
    for code,info in manifest['files'].items():
        raw=(root/(code+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=info['sha256']: raise ValueError('数据校验失败')
        data[code]=json.loads(raw)
    rows=make_samples(data); parts=split_samples(rows)
    if min(len(p) for p in parts)<500: raise ValueError('训练/验证/测试样本不足')
    x=np.array([r['x'] for r in parts[0]],dtype=np.float32); mean=x.mean(0); scale=x.std(0).clip(.0001)
    tensors=[]
    for part in parts:
        xx=np.array([r['x'] for r in part],dtype=np.float32)
        yy=np.array([r['y'] for r in part],dtype=np.float32).reshape(-1,1)
        tensors.append((torch.tensor(np.clip((xx-mean)/scale,-8,8),device=device),torch.tensor(yy,device=device)))
    net=nn.Sequential(nn.Linear(12,32),nn.ReLU(),nn.Linear(32,16),nn.ReLU(),nn.Linear(16,1)).to(device)
    optimizer=torch.optim.AdamW(net.parameters(),lr=.001,weight_decay=.01)
    best=float('inf'); best_state=None; wait=0; started=time.time()
    for epoch in range(180):
        net.train(); xx,yy=tensors[0]
        # Contiguous chronological minibatches, no future leakage across partitions.
        for offset in range(0,len(xx),512):
            optimizer.zero_grad(); loss=nn.functional.huber_loss(net(xx[offset:offset+512]),yy[offset:offset+512],delta=.05)
            loss.backward(); nn.utils.clip_grad_norm_(net.parameters(),1); optimizer.step()
        net.eval()
        with torch.no_grad(): score=nn.functional.mse_loss(net(tensors[1][0]),tensors[1][1]).item()
        if score<best-1e-7: best=score;best_state=copy.deepcopy(net.state_dict());wait=0
        else: wait+=1
        if epoch%10==0: print('epoch',epoch,'validation_mse',score,flush=True)
        if wait>=18: break
        if device=='cuda' and torch.cuda.mem_get_info()[0]<4*1024**3: raise RuntimeError('GPU余量下降，停止本次训练')
    net.load_state_dict(best_state);net.eval()
    layers=[{'weight':l.weight.detach().cpu().tolist(),'bias':l.bias.detach().cpu().tolist()} for l in net if isinstance(l,nn.Linear)]
    model={'schema':1,'feature_version':FEATURE_VERSION,'feature_names':FEATURE_NAMES,'mean':mean.tolist(),'scale':scale.tolist(),
           'layers':layers,'universe':list(data),'trained_through':max(r['label_end'] for r in parts[0]),'threshold':.005,
           'version':'20261021-mlp-v1'}
    report={'model_sha256':'','manifest':manifest,'device':device,'epochs':epoch+1,'training_seconds':round(time.time()-started,2),
            'gpu_peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,2) if device=='cuda' else 0,
            'partitions':[{ 'start':p[0]['date'],'end':p[-1]['date'],'label_end':max(r['label_end'] for r in p),'rows':len(p)} for p in parts],
            'limits':['当前股票池含幸存者及选择偏差','模拟器不处理分红送转；公司行为持仓日计数单列，未计分红，非精确总回报',
                      '训练样本排除复权因子变化窗口；测试组合只按已知历史过滤，不提前知道未来公司行为',
                      '下一日开盘成交近似线上10点分钟成交；费用使用当前模拟盘固定费率，双边滑点10bp',
                      '离线历史结果不代表未来收益；测试仅用于最终报告，不参与模型与阈值选择']}
    def save():
        raw=json.dumps(model,separators=(',',':'),allow_nan=False).encode();(output/'model.json').write_bytes(raw)
        report['model_sha256']=hashlib.sha256(raw).hexdigest();(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    save();strategy=QuantStrategy(output)
    # Threshold selection uses validation only. Test remains frozen.
    candidates=[]
    validation_dates=sorted(set(r['date'] for r in parts[1]))
    for threshold in (.003,.005,.01):
        strategy.model['threshold']=threshold
        result=backtest(data,strategy,validation_dates)
        candidates.append({'threshold':threshold,'total_return':result['total_return'],'max_drawdown':result['max_drawdown']})
    model['threshold']=max(candidates,key=lambda c:c['total_return']-c['max_drawdown'])['threshold'];save();strategy=QuantStrategy(output)
    report['validation_candidates']=candidates
    test_dates=sorted(set(r['date'] for r in parts[2]))
    report['test']={kind:backtest(data,strategy,test_dates,kind) for kind in ('model','momentum','equal_weight')}
    with torch.no_grad():
        torch_values=net(tensors[2][0][:64]).cpu().numpy().ravel()
    differences=[abs(strategy.predict(r['x'])-float(v)) for r,v in zip(parts[2][:64],torch_values)]
    report['export_max_absolute_error']=max(differences)
    if max(differences)>1e-5: raise ValueError('导出推理不一致')
    report['dependencies']={'torch':torch.__version__,'numpy':np.__version__}
    save();print(json.dumps({k:{m:v for m,v in r.items() if m!='equity_curve'} for k,r in report['test'].items()},ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True);parser.add_argument('--output',required=True);parser.add_argument('--device',default='cuda')
    args=parser.parse_args();train(args.data,args.output,args.device)
