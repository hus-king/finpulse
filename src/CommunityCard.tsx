import { Activity, AlertTriangle, ExternalLink, Lightbulb, LoaderCircle, RefreshCw } from 'lucide-react';
import { dateTime } from './format';
import type { Dashboard } from './types';

export default function CommunityCard({ data, running, refresh }: { data?: Dashboard['sentiment']; running: boolean; refresh: () => void }) {
  const source = data?.source === 'eastmoney_direct' ? '东方财富股吧 · 直接采集' : data?.source === 'mixed' ? '股吧直接采集 + 搜索备用' : data?.source === 'tavily_search' ? 'Tavily 搜索备用' : '公开帖子样本';
  const direct = data?.diagnostics?.direct;
  const search = data?.diagnostics?.tavily;
  const stance = { bull: '看多', bear: '看空', neutral: '观望 / 无法判断' };
  const weighted = data?.weighting_method === 'log_interaction';
  const method = weighted ? '对数互动加权' : '等权样本';
  const alertLevel = data?.alert_level ?? (data?.bull && data.bull > 85 ? 'overheated' : data?.bear && data.bear > 80 ? 'frozen' : 'normal');

  return (
    <section className="panel community-card">
      <div className="community-title">
        <h2>社区情绪</h2>
        <span>公开样本</span>
      </div>
      <p className="community-source">
        {source}
        {data?.collected_at && <small>更新于 {dateTime(data.collected_at)}</small>}
      </p>

      {data?.status === 'ok' ? (
        <>
          {alertLevel === 'overheated' && (
            <div className="contrarian-badge overheated">
              <AlertTriangle size={15} />
              <div>
                <strong>样本看多情绪集中</strong>
                <small>{method}看多达 {data.bull}%，当前样本偏多，不构成见顶判断</small>
              </div>
            </div>
          )}
          {alertLevel === 'frozen' && (
            <div className="contrarian-badge frozen">
              <Lightbulb size={15} />
              <div>
                <strong>样本看空情绪集中</strong>
                <small>{method}看空达 {data.bear}%，当前样本偏空，不构成反弹判断</small>
              </div>
            </div>
          )}

          <div className="community-ratios">
            <span>看多<strong className="up">{data.bull}%</strong></span>
            <span>观望<strong>{data.neutral}%</strong></span>
            <span>看空<strong className="down">{data.bear}%</strong></span>
          </div>

          <div className="community-track">
            <span style={{ width: `${data.bull}%` }} />
            <span style={{ width: `${data.neutral}%` }} />
            <span style={{ width: `${data.bear}%` }} />
          </div>

          <p className="community-weight-note">
            {data.sample_count} 条可核验帖子 · {method} · {weighted ? data.alert : '旧版快照，按帖子等权统计'}
          </p>

          <div className="keyword-tags">
            {data.keywords.map(word => <span key={word}>{word}</span>)}
          </div>
        </>
      ) : (
        <div className="community-empty">
          <Activity size={23} />
          <p>{running ? '后台正在处理，已有样本仍可查看' : data?.status === 'insufficient' ? `已取得 ${data.sample_count} 条帖子，样本数量不足` : data?.status === 'empty' ? '没有取得符合条件的独立帖子' : data?.error ?? '尚未采集社区帖子'}</p>
          <small>优先读取股吧公开帖子与原始发布时间，至少 5 条有效样本才生成加权比例。</small>
        </div>
      )}

      <button className="subtle-button community-refresh" disabled={running} onClick={refresh}>
        {running ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
        {running ? '正在处理…' : '更新社区样本'}
      </button>

      {!!data?.posts.length && (
        <details className="community-samples">
          <summary>查看 {data.posts.length} 条样本与原文</summary>
          <div>
            {data.posts.map(post => (
              <article key={post.id}>
                <a href={post.url} target="_blank" rel="noopener noreferrer">
                  {post.title}
                  <ExternalLink size={11} />
                </a>
                <small>
                  {post.date} · {post.stance ? stance[post.stance as keyof typeof stance] : '尚未分类'} · {post.text_source === 'post_body' ? '帖子正文' : '搜索片段'}
                </small>
                <p>{post.content?.slice(0, 180)}</p>
                {(post.views != null || post.replies != null) && (
                  <small>
                    阅读 {post.views ?? '—'} · 回复 {post.replies ?? '—'}
                    {post.weight != null ? ` · 互动权重 x${post.weight.toFixed(1)}` : ' · 等权统计'}
                  </small>
                )}
              </article>
            ))}
          </div>
        </details>
      )}

      {(direct || search) && (
        <details className="community-diagnostics">
          <summary>查看采集结果</summary>
          {direct && (
            <p>{direct.status === 'error' ? '直接采集失败，已尝试备用检索。' : `列表 ${direct.listed ?? 0} 条 · 普通帖子候选 ${direct.eligible ?? 0} 条 · 取得正文 ${direct.retained ?? 0} 条 · 详情失败 ${direct.detail_failed ?? 0} 条`}</p>
          )}
          {search && (
            <p>{search.status === 'error' ? '备用检索失败。' : `备用检索 ${search.listed ?? 0} 条 · 符合条件 ${search.retained ?? 0} 条`}</p>
          )}
        </details>
      )}

      {!!data?.warnings?.length && <p className="community-limitations">{data.warnings.join('；')}</p>}
      <p className="source-note">
        {data?.note ?? '直接采集东方财富股吧，来源不可用或样本不足时尝试 Tavily。结合阅读量与回复数对数加权统计，有限样本不代表全部股民。'}
      </p>
    </section>
  );
}
