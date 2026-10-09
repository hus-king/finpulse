import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { useTheme } from './ThemeContext';
import type { buildIntraday } from './intradayMath';
echarts.use([LineChart,BarChart,GridComponent,TooltipComponent,MarkLineComponent,CanvasRenderer]);
type Session=ReturnType<typeof buildIntraday>;
const ticks=new Set([0,30,60,90,120,151,181,211,241]);
export default function IntradayChart({session,loading}:{session:Session;loading:boolean}) {
  const root=useRef<HTMLDivElement>(null),instance=useRef<ReturnType<typeof echarts.init>|null>(null);
  const {theme}=useTheme();
  useEffect(()=>{
    if(!root.current) return;
    const chart=echarts.init(root.current);instance.current=chart;
    const observer=new ResizeObserver(()=>chart.resize());observer.observe(root.current);
    return ()=>{observer.disconnect();chart.dispose();instance.current=null;};
  },[]);
  useEffect(()=>{
    const chart=instance.current;if(!chart) return;
    const styles=getComputedStyle(document.documentElement);
    const color=(name:string)=>styles.getPropertyValue(`--chart-${name}`).trim();
    const reference=session.previousClose;
    const traded=session.rows.map(row=>row.close);
    const center=reference??session.quote.price??1;
    const span=Math.max(center*.003,...traded.map(price=>Math.abs(price-center)))*1.12;
    const min=Math.max(.0001,center-span),max=center+span;
    const byTime=new Map(session.rows.map(row=>[row.date.slice(11,16),row]));
    const axis={type:'category' as const,data:session.times,boundaryGap:false,axisLine:{lineStyle:{color:color('border')}},axisTick:{show:false},splitLine:{show:false}};
    chart.setOption({animation:false,backgroundColor:'transparent',
      textStyle:{fontFamily:'Segoe UI, Microsoft YaHei, sans-serif'},
      grid:[{left:58,right:56,top:20,bottom:106},{left:58,right:56,height:55,bottom:30}],
      tooltip:{trigger:'axis',renderMode:'richText',axisPointer:{type:'cross',label:{backgroundColor:color('crosshair')}},backgroundColor:color('tooltip-bg'),borderColor:color('border'),textStyle:{color:color('tooltip-text'),fontSize:12}},
      axisPointer:{link:[{xAxisIndex:'all'}]},
      xAxis:[{...axis,axisLabel:{show:false}},{...axis,gridIndex:1,axisLabel:{color:color('text'),fontSize:9,interval:(i:number)=>ticks.has(i),formatter:(value:string,i:number)=>i===120?'11:30/13:00':value}}],
      yAxis:[{type:'value',min,max,splitNumber:4,axisLabel:{show:!!reference||!!traded.length,color:color('text'),fontSize:10,formatter:(v:number)=>v.toFixed(2)},axisLine:{show:false},splitLine:{lineStyle:{color:color('grid'),type:'dashed'}}},
        {type:'value',gridIndex:1,min:0,splitNumber:1,axisLabel:{color:color('text'),fontSize:9,formatter:(v:number)=>`${(v/10000).toFixed(1)}万`},splitLine:{show:false},axisLine:{show:false}},
        {type:'value',gridIndex:0,position:'right',min:reference?(min/reference-1)*100:0,max:reference?(max/reference-1)*100:1,splitNumber:4,axisLabel:{show:!!reference,color:color('text'),fontSize:9,formatter:(v:number)=>`${v>0?'+':''}${v.toFixed(2)}%`},axisLine:{show:false},splitLine:{show:false}}],
      series:[{id:'intraday-price',name:'分钟价格',type:'line',data:session.prices,connectNulls:false,showSymbol:false,lineStyle:{color:'#3b82f6',width:1.8},itemStyle:{color:'#3b82f6'},areaStyle:{color:'#3b82f6',opacity:.09},
        ...(reference?{markLine:{silent:true,symbol:'none',label:{show:false},lineStyle:{color:color('text'),type:'dashed',opacity:.6},data:[{yAxis:reference}]}}:{})},
        {id:'intraday-volume',name:'分钟成交量（股）',type:'bar',xAxisIndex:1,yAxisIndex:1,barMaxWidth:4,data:session.volumes.map((value,i)=>{
          const row=byTime.get(session.times[i]);
          return {value,itemStyle:{color:row&&row.close>=row.open?color('volume-up'):color('volume-down'),opacity:.7}};
        })}],
    },{notMerge:true});
  },[session,theme]);
  return <div className="intraday-chart"><div className="price-chart" ref={root} role="img" aria-label={`${session.day??'等待交易日'}分时走势，09:30开盘至15:00收盘，11:30至13:00午间休市折叠，未来时段和缺失分钟留空`} />
    {!session.rows.length&&<div className="intraday-empty" role="status"><strong>{loading?'正在获取分钟行情':'该交易日尚无分钟数据'}</strong><span>{session.day??'等待交易日确认'} · 已保留完整交易时间轴</span></div>}
    <div className="intraday-axis-note">09:30 — 15:00 · 午间休市折叠{session.previousClose!=null?` · 参考收盘价 ${session.previousClose.toFixed(2)}`:''}</div>
  </div>;
}
