import { useEffect, useState } from 'react';
import { CheckCircle2, LoaderCircle, Plus, Search } from 'lucide-react';
import { api } from './api';
import type { Stock } from './types';

interface SearchResult { items: Stock[]; total: number; scope: string; warnings: string[] }
interface Props {
  watchlist: string[]; disabled: boolean;
  select: (stock: Stock) => void; add: (stock: Stock) => Promise<boolean>;
  remove: (code: string) => Promise<void>;
}

export default function StockSearch({ watchlist, disabled, select, add, remove }: Props) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pending, setPending] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const timer = window.setTimeout(() => {
      api<SearchResult>(`/api/stocks?q=${encodeURIComponent(query.trim())}&limit=30`, undefined, controller.signal)
        .then(value => { if (!controller.signal.aborted) setResult(value); })
        .catch(e => { if (!controller.signal.aborted) setError(e.message); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 250);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query]);

  async function toggle(stock: Stock) {
    setPending(stock.code); setError('');
    try { if (watchlist.includes(stock.code)) await remove(stock.code); else await add(stock); }
    catch (e) { setError((e as Error).message); }
    finally { setPending(''); }
  }

  return <>
    <label className="stock-search"><Search size={18} /><input autoFocus aria-label="搜索股票" placeholder="股票名称、6 位代码或拼音缩写，如 招商银行 / 600036 / ZSYH" value={query} onChange={e => setQuery(e.target.value)} /></label>
    <p className="supported-caption">{result ? `${result.scope} · 已收录 ${result.total.toLocaleString()} 只` : '正在获取交易所股票列表，首次加载可能需要约一分钟…'}</p>
    <div className="auto-collect-note"><strong>添加后，自动准备研究资料</strong><p>保存到你的账号 → 获取真实历史 K 线 → 搜索近 30 天新闻 → 清洗去重 → AI 研判最多 3 篇。可继续浏览，并查看后台处理进度。</p></div>
    {error && <p className="search-error" role="alert">{error}</p>}
    {!!result?.warnings.length && <details className="search-source-warning"><summary>部分股票列表源暂不可用，正在使用已缓存列表</summary>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}</details>}
    {loading ? <div className="stock-search-loading" role="status"><LoaderCircle size={18} className="spin" />正在搜索真实股票…</div> : <div className="catalog-list">{result?.items.map(stock => <div key={stock.code}><button onClick={() => select(stock)}><strong>{stock.name}</strong><small>{stock.exchange} {stock.code} · {stock.industry}</small></button><button disabled={disabled || !!pending} onClick={() => void toggle(stock)}>{pending === stock.code ? <LoaderCircle size={15} className="spin" /> : watchlist.includes(stock.code) ? <CheckCircle2 size={15} /> : <Plus size={15} />}{pending === stock.code ? '正在添加…' : watchlist.includes(stock.code) ? '已关注 · 移除' : '添加并采集'}</button></div>)}{result && !result.items.length && <p className="rail-empty">未找到匹配的沪深 A 股，试试完整名称或 6 位代码。</p>}</div>}
    <p className="supported-caption">点击股票名称只切换查看。每个账号最多关注 20 只；数据源失败会显示原因，可稍后重试采集。</p>
  </>;
}
