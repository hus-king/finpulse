"""Local human-review preview of the fixed sample; no AI-generated scores."""
import html
import re
from pathlib import Path
from urllib.parse import urlsplit

SUMMARIES = {
    "g1-r2": "报道段永平买入茅台及盘中股价变化。三家媒体对同一事件的报道已合并，保留来源链接。",
    "g2-r1": "材料讨论向控股子公司提供财务资助，包括资金用途、股权关系及董事会意见。",
    "g2-r2": "公告 PDF 涉及2026年度第八期绿色科技创新债券发行完成，保留公司和债券事项。",
    "g2-r3": "报道临时股东会安排，以及员工持股计划、财务资助等拟审议事项。",
    "g2-r4": "报道拟推出第二期员工持股计划，属于公告相关信息。另一个来源存在跨日的时间元数据冲突，暂时隔离。",
    "g4-r1": "报道与中芯国际股票相关的牛证触发强制赎回，属于衍生品事件，不是公司经营公告。",
    "g4-r2": "报道中芯国际盘中价格、成交额和资金流向。这是盘中快讯，下方的日线数据是收盘记录，时点不同。",
    "g4-r3": "报道机构对中芯国际的评级与目标价，属于机构观点，不是已实现的经营结果。",
}


def esc(value):
    return html.escape(str(value), quote=True)


def source_link(url, title):
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(title)} ↗</a>' if urlsplit(url).scheme in ("https", "http") else esc(title)


def sparkline(rows):
    if len(rows) < 2:
        return ""
    prices = [row["close"] for row in rows]
    low, high = min(prices), max(prices)
    points = " ".join(f'{4+index/(len(prices)-1)*312:.1f},{58-(price-low)/max(high-low, .01)*50:.1f}' for index, price in enumerate(prices))
    return f'<svg viewBox="0 0 320 64" role="img" aria-label="本次返回的历史日线收盘走势"><polyline points="{points}" fill="none" stroke="#4dd6b1" stroke-width="2"/></svg>'


def render_preview(report, destination):
    existing = destination.parent/"index.html"
    original = existing.read_text(encoding="utf-8") if existing.exists() else ""
    match = re.search(r"<style>(.*?)</style>", original, flags=re.S)
    base_css = match[1] if match else "body{background:#0b111b;color:#e4edf6;font:15px/1.7 sans-serif}main{max-width:1180px;margin:auto;padding:28px}a{color:#4dd6b1}.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}.result{padding:20px;border:1px solid #263446}"
    extra_css = '.quotes{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:25px 0}.quote{padding:22px;background:var(--panel,#111c29);border:1px solid var(--line,#263446);border-radius:12px}.price{font-size:29px;font-weight:700}.quote small{display:block;color:var(--muted,#90a3b9)}.quote svg{display:block;width:100%;height:70px;margin-top:8px}.up{color:#fa8594}.down{color:#4dd6b1}.context{margin:12px 0;padding:10px;border:1px solid #263446;border-radius:7px;font-size:12px;color:#b9c8d9}.table-wrap{overflow:auto;margin-top:18px}table{width:100%;border-collapse:collapse;min-width:850px;font-size:12px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #263446;padding:13px 10px}th{color:#90a3b9}td:first-child{white-space:nowrap}.audit-title{max-width:440px}.scope{border-left:3px solid #4dd6b1;padding-left:14px;color:#b9c8d9}.date-note{color:#e9bc71;font-size:12px}.result details{font-size:12px;color:#90a3b9;margin-top:10px}.result details a{display:block;color:#4dd6b1;margin-top:8px}.jump{color:#4dd6b1;font-size:12px;margin-left:auto;align-self:center}.status-retained{color:#4dd6b1}.status-merged{color:#8eb6f6}.status-filtered{color:#e9bc71}@media(max-width:720px){.quotes{grid-template-columns:1fr}.jump{margin-left:0}.cards{grid-template-columns:1fr}}'
    counts = report["cleaning"]["summary"]
    quotes = []
    for q in report["market"]["snapshots"]:
        price = f'{q["close"]:,.2f}' if q["close"] is not None else "获取失败"
        percent = q["change_percent"]
        change = f'{percent:+.2f}%' if percent is not None else "—"
        quotes.append(f'<article class="quote" data-stock="{esc(q["stock"])}"><b>{esc(q["stock"])} <span class="meta">{q["stock_code"]}</span></b><div class="price">{price} <span class="{"up" if percent is not None and percent>=0 else "down"}" style="font-size:14px">{change}</span></div><small>最近返回日线收盘 · {esc(q["as_of_date"])} · 元</small>{sparkline(q["history"])}<small>AkShare / 腾讯 · {q["history_rows"]} 根日线 · 未复权</small><small>雪球最新报价：{esc(q["quote_status"])} {esc(q["quote_error_type"] or "")}</small></article>')
    cards = []
    for item in report["cleaning"]["items"]:
        context = item["market_context"]
        bar = context["news_calendar_day_bar"]
        day_price = f'{bar["close"]:,.2f} 元' if bar else "本次日线中没有该日记录"
        stats = item["text_stats"]
        note = "正文日期与搜索日期一致" if item["date_status"] == "body_verified" else "仅有搜索发布时间，正文发布日期尚未核验"
        source_links = "".join(source_link(s["url"], s["title"]) for s in item["sources"])
        summary = SUMMARIES.get(item["id"], "相关内容可通过来源链接和清洗后的 JSON 查看。")
        cards.append(f'<article class="result" data-stock="{esc(item["stock"])}"><div class="result-top"><span class="rank">{esc(item["stock"])}</span><span class="tag {"good" if item["tier"]=="company" else "neutral"}">{esc(item["category_label"])}</span><span class="score">{len(item["sources"])} 个来源</span></div><h3>{source_link(item["url"], item["title"])}</h3><div class="meta">{esc(item["effective_date"])} · {esc(item["text_source"])}</div><p class="summary">{esc(summary)}</p><div class="context">同一日历日期的收盘记录：{day_price}<br>最新返回收盘：{context["latest_daily_close"]} 元（{esc(context["latest_bar_date"])}）<br>仅按股票关联，未对齐新闻发布时刻。</div><div class="meta">提取文本 {stats["before_chars"]:,} → 清洗后 {stats["after_chars"]:,} 字符</div><div class="date-note">{esc(note)}</div><details><summary>查看合并保留的来源</summary>{source_links}</details></article>')
    rows = []
    for item in report["cleaning"]["audit"]:
        status = {"retained": "保留", "merged": "合并", "filtered": "过滤 / 隔离"}[item["status"]]
        reason = "；".join(item["reasons"]) or ("辅助材料，单独标记" if item["tier"]=="auxiliary" else "通过本次规则")
        if item["status"] == "merged":
            reason += f'；归入 {item["canonical_id"]}'
        dates = f'搜索：{item["published_date_metadata"] or "无"}<br>正文：{item["published_date_body"] or "未核验"}'
        rows.append(f'<tr data-stock="{esc(item["stock"])}"><td>{item["id"]}<br>{esc(item["stock"])}</td><td class="audit-title">{source_link(item["url"], item["title"])}</td><td class="status-{item["status"]}">{status}</td><td>{esc(reason)}</td><td>{dates}</td></tr>')
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FinPulse · 新闻清洗与 AkShare 实测</title><style>{base_css}{extra_css}</style></head><body><main><div class="brand">FinPulse<span>新闻清洗 + AKSHARE · 实测对照</span></div><h1>从搜索结果，到可用材料</h1><p class="intro">新闻样本来自 10 月 1 日的 Tavily 搜索；本次实验参考日期为 {esc(report["reference_date"])}。内容概览为人工核阅摘要，未调用大模型打分。</p><div class="overview"><div class="metric"><b>{counts["input"]}</b><span>原始搜索结果</span></div><div class="metric"><b>{counts["retained"]}</b><span>保留的独立事件</span></div><div class="metric"><b>{counts["merged"]}</b><span>合并的重复报道</span></div><div class="metric"><b>{counts["filtered"]}</b><span>过滤或暂时隔离</span></div></div><div class="notice"><strong>清洗结果：</strong>{counts["company_events"]} 条公司相关新闻 + {counts["auxiliary_events"]} 条辅助材料。原文为2025年的旧新闻已过滤；另一条相差一天的时间冲突可能与时区有关，暂时隔离。保留结果中仍有两条发布日期仅来自搜索元数据。</div><nav aria-label="按股票筛选"><button data-stock-filter="all" class="active">全部</button><button data-stock-filter="贵州茅台">贵州茅台</button><button data-stock-filter="宁德时代">宁德时代</button><button data-stock-filter="中芯国际">中芯国际</button><a class="jump" href="#audit">查看全部 20 条处理记录 ↓</a></nav><section><div class="section-head" style="margin-top:28px"><div><span class="eyebrow">AKSHARE · ACTUAL API CALLS</span><h2>三只股票的真实日线</h2></div><div class="stats">AkShare {esc(report["market"]["akshare_version"])}<br>数据来源：腾讯</div></div><div class="quotes">{''.join(quotes)}</div><p class="scope">本次最新数据日期均为 2026-09-30，展示最近返回的日线收盘价。雪球最新报价接口返回 APIError；这里没有把历史收盘价标成实时价格。每只股票返回21根日线，成交量单位为股，成交额为元。折线展示这批日线的收盘走势。</p></section><section id="retained"><span class="eyebrow">CLEANED NEWS</span><h2>保留的新闻与关联行情</h2><p class="intro" style="margin-bottom:18px">清理导航与页脚、核对可识别的发布时间，再按同一股票、相近日期和标题相似度合并事件；机构观点、行情快讯、衍生品消息保留类别标记。</p><div class="cards">{''.join(cards)}</div></section><section class="extraction" id="audit"><span class="eyebrow">BEFORE / AFTER</span><h2>每条结果为什么留下或移除？</h2><p>日期冲突的结果先隔离。只在正文的发布信息中取日期，文章提到的财报年份不会直接被当作发布日期。</p><div class="table-wrap"><table><thead><tr><th>编号 / 股票</th><th>原始标题与来源</th><th>处理</th><th>原因</th><th>日期核对</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section><footer>原始搜索预览：<a href="./">返回上一轮搜索结果 ↗</a><br>完整清洗文本与结构化行情：.runtime/news-cleaned-akshare-{esc(report["reference_date"])}.json<br>清洗规则为本次试验版本，尚未进行全面标注评估。新闻与价格并列展示，不用于证明因果关系。</footer></main><script>const filters=document.querySelectorAll('[data-stock-filter]');filters.forEach(button=>button.addEventListener('click',()=>{{const value=button.dataset.stockFilter;filters.forEach(b=>b.classList.toggle('active',b===button));document.querySelectorAll('[data-stock]').forEach(item=>item.hidden=value!=='all'&&item.dataset.stock!==value);}}));</script></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination
