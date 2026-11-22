import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, LoaderCircle, Plus, Search } from 'lucide-react';
import { api } from './api';
import { displayCode, isIndex } from './instruments';
import type { Stock } from './types';
import './stock-search.css';

interface SearchResult { items: Stock[]; total: number; warnings: string[] }
interface Props {
  watchlist: string[]; disabled: boolean;
  select: (stock: Stock) => void; add: (stock: Stock) => Promise<boolean>;
  remove: (code: string) => Promise<void>;
}

export default function StockSearch({ watchlist, disabled, select, add, remove }: Props) {
  const [query, setQuery] = useState('');
  const [assetType, setAssetType] = useState('all');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [pending, setPending] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const term = query.trim();

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setError('');
    setActionError('');
    if (list.current) list.current.scrollTop = 0;
    const timer = window.setTimeout(() => {
      api<SearchResult>(`/api/stocks?q=${encodeURIComponent(term)}&limit=30&asset_type=${assetType}`, undefined, controller.signal)
        .then(value => { if (!controller.signal.aborted) setResult(value); })
        .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    }, term ? 160 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [term, assetType]);

  async function toggle(stock: Stock) {
    if (disabled || pending) return;
    setPending(stock.code);
    setActionError('');
    try { if (watchlist.includes(stock.code)) await remove(stock.code); else await add(stock); }
    catch (reason) { setActionError((reason as Error).message); }
    finally { setPending(''); }
  }

  function handleSearchKey(event: React.KeyboardEvent<HTMLInputElement>) {
    const first = result?.items[0];
    if (!first || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      list.current?.querySelector<HTMLButtonElement>('.catalog-name')?.focus();
    } else if (event.key === 'Enter' && term) {
      event.preventDefault();
      select(first);
    }
  }

  function handleResultKey(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    const row = event.currentTarget.parentElement;
    const adjacent = event.key === 'ArrowDown' ? row?.nextElementSibling : row?.previousElementSibling;
    const name = adjacent?.querySelector<HTMLButtonElement>('.catalog-name');
    if (name) name.focus();
    else if (event.key === 'ArrowUp') input.current?.focus();
  }

  return <>
    <label className="stock-search"><Search size={18} aria-hidden="true" /><input ref={input} aria-label="搜索股票" placeholder="输入股票或指数名称、代码、拼音" value={query} maxLength={80} onKeyDown={handleSearchKey} onChange={event => setQuery(event.target.value)} /></label>
    <div className="stock-search-options">
      <div className="instrument-filters" role="group" aria-label="标的类型">{[['all', '全部'], ['stock', 'A股'], ['index', '指数']].map(([value, label]) => <button key={value} type="button" aria-pressed={assetType === value} onClick={() => setAssetType(value)}>{label}</button>)}</div>
      <span className="stock-search-count" role="status">{result && (term ? `显示 ${result.items.length} 个结果` : `已收录 ${result.total.toLocaleString()} 个标的`)}</span>
    </div>
    {actionError && <p className="search-error" role="alert">{actionError}</p>}
    <div ref={list} className="catalog-list" aria-label="搜索结果">
      {error && <p className="search-error" role="alert">{error}</p>}
      {!result && !error && <p className="stock-search-loading" role="status"><LoaderCircle size={18} className="spin" />正在搜索…</p>}
      {!!result?.warnings.length && <details className="search-source-warning"><summary>部分数据源暂不可用，已使用缓存</summary>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}</details>}
      {result?.items.map(stock => {
        const followed = watchlist.includes(stock.code);
        const working = pending === stock.code;
        return <div key={stock.code}>
          <button className="catalog-name" onKeyDown={handleResultKey} onClick={() => select(stock)}><strong id={`catalog-${stock.code}`}>{stock.name}</strong><small>{stock.exchange} {displayCode(stock)} · {stock.industry}</small></button>
          <button aria-describedby={`catalog-${stock.code}`} aria-disabled={disabled || !!pending} onClick={() => void toggle(stock)}>
            {working ? <LoaderCircle size={15} className="spin" /> : followed ? <CheckCircle2 size={15} /> : <Plus size={15} />}
            {working ? followed ? '正在移除…' : '正在添加…' : followed ? '移出自选' : isIndex(stock) ? '关注指数' : '添加并采集'}
          </button>
        </div>;
      })}
      {result && !result.items.length && <p className="rail-empty">未找到匹配标的，试试其他名称、代码或类型。</p>}
    </div>
    <p className="supported-caption stock-search-footnote">自选上限 20 个。添加 A 股后将自动采集并研判新闻。</p>
  </>;
}
