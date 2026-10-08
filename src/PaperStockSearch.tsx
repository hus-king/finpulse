import { useEffect, useState } from 'react';
import { LoaderCircle, Search } from 'lucide-react';
import { api } from './api';
import type { Stock } from './types';

export default function PaperStockSearch({ onSelect }: { onSelect: (stock: Stock) => void }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<Stock[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const timer = window.setTimeout(() => {
      api<{ items: Stock[] }>(`/api/stocks?q=${encodeURIComponent(query.trim())}&limit=20`, undefined, controller.signal)
        .then(result => { if (!controller.signal.aborted) setItems(result.items.filter(stock => !stock.code.startsWith('689'))); })
        .catch(error => { if (!controller.signal.aborted) setError(error.message); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 250);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query]);
  return <div className="paper-stock-picker">
    <label><Search size={16} /><input autoFocus aria-label="搜索交易股票" placeholder="股票名称、代码或拼音" value={query} onChange={event => setQuery(event.target.value)} /></label>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status"><LoaderCircle size={16} className="spin" />正在搜索…</p> : <div className="paper-stock-results">{items.map(stock => <button type="button" key={stock.code} onClick={() => onSelect(stock)}><strong>{stock.name}</strong><span>{stock.exchange} {stock.code}</span></button>)}{!items.length && <p>没有找到匹配股票。</p>}</div>}
    <small>仅选择交易股票，不触发新闻或 AI 采集。</small>
  </div>;
}
