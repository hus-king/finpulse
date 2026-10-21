"""Bounded Tencent daily data adapter; same upstream as AkShare hist_tx."""
import argparse
import hashlib
import json
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler

UNIVERSE = ['000001','000002','000063','000333','000651','000725','000858','002027','002142','002415',
            '002475','002594','300015','300059','300122','300750','600000','600009','600030','600031',
            '600036','600276','600309','600519','600887','601088','601166','601318','601398','601899']
URL='https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get'


def fetch_history(code, start, end, adjust=''):
    symbol=('sh' if code.startswith('6') else 'sz')+code
    rows={}
    opener=build_opener(ProxyHandler({}))
    for year in range(int(start[:4]),int(end[:4])+1,2):
        params={'param':f'{symbol},day,{year}-01-01,{min(year+1,int(end[:4]))}-12-31,640,{adjust}'}
        error=None
        for attempt in range(3):
            try:
                with opener.open(Request(URL+'?'+urlencode(params),headers={'User-Agent':'FinPulse academic research'}),timeout=15) as response:
                    data=json.loads(response.read(2_000_000))['data'][symbol]
                bars=data.get(adjust+'day',data.get('day',[]))
                if not isinstance(bars,list) or not bars:
                    raise ValueError('日线源返回空数据')
                for r in bars:
                    if start<=r[0]<=end:
                        row={'date':r[0], **{k:float(v) for k,v in zip(['open','close','high','low','volume'],r[1:6])}}
                        rows[r[0]]=row
                break
            except Exception as exc:
                error=exc
                if attempt==2: raise RuntimeError(f'{code} {year}: 行情采集失败 {type(exc).__name__}') from exc
                time.sleep(2**attempt)
        time.sleep(.25)
    result=sorted(rows.values(),key=lambda r:r['date'])
    if len(result)<65: raise ValueError(f'{code} 历史数据不足')
    return result


def collect(root, start='2018-01-01', end=None):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    end=end or date.today().isoformat()
    manifest={'source':URL,'start':start,'end':end,'universe':UNIVERSE,'files':{},'errors':{},'selection_bias':'固定当前股票池，含幸存者及选择偏差；不代表全市场'}
    for code in UNIVERSE:
        path=root/(code+'.json')
        try:
            if not path.exists():
                data={kind:fetch_history(code,start,end,adj) for kind,adj in [('raw',''),('adjusted','qfq')]}
                path.write_text(json.dumps(data,separators=(',',':')))
            data=json.loads(path.read_text())
            raw=data['raw']; adjusted=data['adjusted']
            if [b['date'] for b in raw]!=[b['date'] for b in adjusted]: raise ValueError('复权日期不一致')
            manifest['files'][code]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':len(raw),'start':raw[0]['date'],'end':raw[-1]['date']}
            print(code,len(raw),raw[0]['date'],raw[-1]['date'],flush=True)
        except Exception as exc:
            manifest['errors'][code]=str(exc)
            print('FAILED',code,str(exc),flush=True)
        (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    if manifest['errors']: raise RuntimeError('存在未成功采集的股票，禁止训练不完整股票池')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True); parser.add_argument('--end',required=True)
    args=parser.parse_args(); collect(args.output,end=args.end)
