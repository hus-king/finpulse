import type { Candle } from './types';
export interface SessionMarket {
  is_trade_day: boolean | null; state: string; server_time: string; expected_data_time: string | null;
}
export function initialChartPeriod(_saved?: unknown) { return '分时'; }
export function sessionSlots(): string[] {
  return [[570,690],[780,900]].flatMap(([start,end])=>Array.from({length:end-start+1},(_,i)=>`${Math.floor((start+i)/60).toString().padStart(2,'0')}:${((start+i)%60).toString().padStart(2,'0')}`));
}
export function sessionIndex(clock: string): number | null {
  if (!/^\d{2}:\d{2}(?::\d{2})?$/.test(clock)) return null;
  const hour=Number(clock.slice(0,2)),minute=Number(clock.slice(3,5));
  if(hour>23||minute>59) return null;
  const value=hour*60+minute;
  if(value>=570&&value<=690) return value-570;
  if(value>=780&&value<=900) return value-780+121;
  return null;
}
function shanghaiStamp(value: string): string | null {
  const date=new Date(value);
  if(!Number.isFinite(date.getTime())) return null;
  return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date);
}
export function selectSessionDay(market: SessionMarket, candles: Candle[]): string | null {
  const today=shanghaiStamp(market.server_time)?.slice(0,10);
  if(market.is_trade_day===true) return today ?? null;
  if(market.is_trade_day===false && market.expected_data_time) return shanghaiStamp(market.expected_data_time)?.slice(0,10) ?? null;
  return candles.map(row=>row.date.slice(0,10)).filter(day=>/^\d{4}-\d{2}-\d{2}$/.test(day)&&!!today&&day<=today).sort().at(-1) ?? null;
}
export function sessionPreviousClose(day: string | null, daily: Candle[], minutes: Candle[]): number | null {
  if(!day) return null;
  const valid=(row:Candle)=>row.date.slice(0,10)<day && Number.isFinite(row.close)&&row.close>0;
  // Prefer a complete previous session minute close, then the returned daily close.
  const minute=[...minutes].filter(row=>valid(row)&&row.date.slice(11,16)==='15:00').sort((a,b)=>a.date.localeCompare(b.date)).at(-1);
  const bar=[...daily].filter(valid).sort((a,b)=>a.date.localeCompare(b.date)).at(-1);
  return minute && (!bar||minute.date.slice(0,10)>=bar.date.slice(0,10)) ? minute.close : bar?.close ?? null;
}
export function buildIntraday(candles:Candle[],market:SessionMarket,previousClose:number|null) {
  const day=selectSessionDay(market,candles), times=sessionSlots();
  const now=shanghaiStamp(market.server_time);
  // Source 1m timestamps label interval ends; permit only the currently forming minute.
  const cutoff=now ? now.slice(0,10)+' '+(()=>{const m=Number(now.slice(11,13))*60+Number(now.slice(14,16))+(Number(now.slice(17,19))>0?1:0);return `${Math.floor(m/60).toString().padStart(2,'0')}:${(m%60).toString().padStart(2,'0')}:00`;})() : '';
  const unique=new Map<string,Candle>();
  for(const row of candles) {
    if(!day||row.date.slice(0,10)!==day||sessionIndex(row.date.slice(11,16))===null||(now&&row.date>cutoff)) continue;
    if(![row.open,row.close,row.high,row.low,row.volume].every(Number.isFinite)||Math.min(row.open,row.close,row.low,row.high)<=0||row.volume<0||row.high<Math.max(row.open,row.close)||row.low>Math.min(row.open,row.close)) continue;
    unique.set(row.date,row);
  }
  const rows=[...unique.values()].sort((a,b)=>a.date.localeCompare(b.date));
  const prices:(number|null)[]=times.map(()=>null),volumes:(number|null)[]=times.map(()=>null);
  for(const row of rows) {const index=sessionIndex(row.date.slice(11,16))!;prices[index]=row.close;volumes[index]=row.volume;}
  const reference=previousClose!=null&&Number.isFinite(previousClose)&&previousClose>0?previousClose:null;
  const latest=rows.at(-1);
  const quote={price:latest?.close??null,change:latest&&reference?Math.round((latest.close/reference-1)*10000)/100:null,
    open:rows[0]?.open??null,high:rows.length?Math.max(...rows.map(row=>row.high)):null,low:rows.length?Math.min(...rows.map(row=>row.low)):null,volume:rows.length?rows.reduce((sum,row)=>sum+row.volume,0):null};
  return {day,times,rows,prices,volumes,previousClose:reference,quote,as_of:latest?.date??null};
}
