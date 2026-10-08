import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, ChevronRight, Copy, LoaderCircle, MessageSquare, Sparkles, X } from 'lucide-react';
import AnalysisEvidence from './AnalysisEvidence';
import { analysisLabel, isScored } from './analysisPresentation';
import { api } from './api';
import { useAuth } from './AuthContext';
import useDialogScroll from './useDialogScroll';
import type { ModelReply, News, Stock } from './types';

interface Entry { role: 'user' | 'assistant'; content: string; result?: ModelReply }
export default function AiDrawer({ stock, news, onClose, onResult }: { stock: Stock; news?: News; onClose: () => void; onResult: (result: ModelReply) => void }) {
  const { user, requireAuth } = useAuth();
  useDialogScroll();
  const [entries, setEntries] = useState<Entry[]>(() => news && isScored(news) && news.analysis ? [{ role: 'assistant', content: JSON.stringify(news.analysis), result: { content: JSON.stringify(news.analysis), analysis: news.analysis, model: 'saved', elapsed_ms: 0, request_id: 'saved', usage: {}, cached: true } }] : []);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const controller = useRef<AbortController | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { textarea.current?.focus(); return () => controller.current?.abort(); }, []);
  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' }); }, [entries, busy, error]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => { window.removeEventListener('keydown', handler); };
  }, [onClose]);

  async function send(question = input) {
    if (!question.trim() || busy) return;
    if (!requireAuth('登录后即可向 AI 提问。你的问题会保留在输入框中。')) { setInput(question); return; }
    const userEntry: Entry = { role: 'user', content: question.trim() };
    setBusy(true); setError(''); setInput('');
    const history = [...entries, userEntry];
    setEntries(history);
    controller.current = new AbortController();
    try {
      const result = await api<ModelReply>('/api/chat', { stock_code: stock.code, messages: history.slice(-20).map(({ role, content }) => ({ role, content: content.slice(0, 10000) })) }, controller.current.signal);
      setEntries([...history, { role: 'assistant', content: result.content, result }]);
      onResult(result);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) { setError((error as Error).message); setInput(question); setEntries(history.slice(0, -1)); }
    } finally { setBusy(false); }
  }

  async function analyze() {
    if (!news || busy) return;
    if (!requireAuth('登录后即可生成这条新闻的 AI 研判。')) return;
    setBusy(true); setError('');
    controller.current = new AbortController();
    try {
      const result = await api<ModelReply>(`/api/news/${stock.code}/${news.id}/analyze`, {}, controller.current.signal);
      setEntries(previous => [...previous, { role: 'user', content: `研判这条新闻：${news.title}` }, { role: 'assistant', content: result.content, result }]);
      onResult(result);
    } catch (error) { if (!(error instanceof DOMException && error.name === 'AbortError')) setError((error as Error).message); }
    finally { setBusy(false); }
  }

  async function copyResult(content: string) {
    try { await navigator.clipboard.writeText(content); setCopied(true); window.setTimeout(() => setCopied(false), 2000); }
    catch { setError('复制失败，可直接选中文字复制。'); }
  }

  return <div className="drawer-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="ai-drawer" role="dialog" aria-modal="true" aria-labelledby="ai-title">
      <header className="drawer-header"><div className="icon-square mint"><Sparkles size={20} /></div><div><h2 id="ai-title">FinPulse AI</h2><p>{stock.name} · {stock.code}</p></div><button className="icon-button" onClick={onClose} aria-label="关闭 AI 助手"><X size={20} /></button></header>
      <div className="drawer-scroll" ref={scroll}>
        <div className="assistant-intro"><span className="eyebrow">YOUR RESEARCH COMPANION</span><h3>让信息，更有逻辑。</h3><p>基于已采集材料分析新闻、解释影响路径，或提出你的问题。</p><div className="live-label"><span className="status-dot" /> 真实来源与模型研判</div></div>
        {news && <div className="context-card"><span className="eyebrow">正在关注的真实新闻</span><h4>{news.title}</h4><a href={news.url} target="_blank" rel="noopener noreferrer">{news.source} · {news.time} · 查看原文 ↗</a><p className="article-source-note">{news.text_source === 'extracted_body' ? '已提取正文' : '搜索片段，可能不完整'} · {news.date_status === 'body_verified' ? '正文日期已核对' : '仅来源元数据日期'}</p><details><summary>查看清洗后的材料</summary><p>{news.content}</p></details><button className="primary-button" onClick={analyze} disabled={busy}><Sparkles size={15} /> {busy ? '等待模型响应…' : user ? isScored(news) ? '核验研判（优先缓存）' : '生成 AI 研判' : '登录后生成研判'}<ChevronRight size={15} /></button></div>}
        {entries.length === 0 && <div className="suggestions"><span>你可以这样问</span>{['概括已采集新闻的关键事实', '这些消息可能通过哪些路径影响企业？', '有哪些信息还需要进一步核实？'].map(question => <button key={question} disabled={busy} onClick={() => send(question)}><MessageSquare size={14} />{question}<ChevronRight size={14} /></button>)}</div>}
        {entries.map((entry, i) => <div key={i} className={`chat-entry ${entry.role}`}><div className="chat-author">{entry.role === 'user' ? '你' : <><Sparkles size={13} /> FinPulse AI <span className="tiny-tag">{entry.result?.cached ? '已保存研判' : '模型响应'}</span></>}</div>{entry.result?.analysis ? <div className="analysis-result"><span className={`score-label ${(entry.result.analysis.sentiment_score ?? 0) > 0 ? 'positive' : (entry.result.analysis.sentiment_score ?? 0) < 0 ? 'negative' : 'neutral'}`}>{analysisLabel(entry.result.analysis)}</span><h4>{entry.result.analysis.summary}</h4><AnalysisEvidence analysis={entry.result.analysis} /><div className="causal-chain">{entry.result.analysis.causal_chain.map((step, index) => <div key={index}><span>{String(index + 1).padStart(2, '0')}</span><p>{step}</p></div>)}</div><p className="uncertainty">{entry.result.analysis.uncertainty}</p></div> : <div className="chat-content">{entry.content}</div>}{entry.result && <div className="response-meta"><span>{entry.result.request_id === 'saved' ? '来自数据库中已保存的分析' : `${(entry.result.elapsed_ms / 1000).toFixed(1)}s · ${entry.result.usage.total_tokens ?? '—'} tokens${entry.result.cached ? ' · 缓存复用' : ''}`}</span><button onClick={() => copyResult(entry.content)} aria-label="复制响应">{copied ? <Check size={13} /> : <Copy size={13} />}</button></div>}</div>)}
        {busy && <div className="thinking"><LoaderCircle className="spin" size={17} /> 正在请求模型，请稍候…</div>}
        {error && <div className="error-box" role="alert">{error}</div>}
      </div>
      <form className="chat-form" onSubmit={event => { event.preventDefault(); void send(); }}><div className="chat-input"><textarea ref={textarea} aria-label="向 AI 提问" placeholder="输入问题，探索更多线索…" maxLength={6000} value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} /><button className="send-button" type="submit" disabled={busy || !input.trim()} aria-label="发送问题">{busy ? <LoaderCircle className="spin" size={17} /> : <ArrowUp size={19} />}</button></div><p>Enter 发送 · Shift + Enter 换行 · AI 结论需结合实际信息核验</p></form>
    </section>
  </div>;
}
