"""Self-contained read-only reviewer for the development evidence pipeline."""
import argparse
import json
from pathlib import Path

from .evidence import article_packet, company_packet
from .storage import read_jsonl


def render(summary, queue, articles):
    data = json.dumps({'summary':summary,'queue':queue,'articles':articles},ensure_ascii=False)
    data = data.replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>FinPulse 新闻证据复核 · v9 开发材料</title>
<style>body{background:#11161e;color:#dce4ed;font:15px system-ui;margin:28px}h1{font-size:25px}header{max-width:1400px;margin:auto}select{padding:10px;background:#202b39;color:inherit;border:1px solid #51667d;border-radius:8px;max-width:100%}main{display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:1400px;margin:20px auto}section{background:#1b2430;padding:20px;border-radius:12px;min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}small{color:#aab9c9}summary{cursor:pointer;color:#9dd4fc}details{margin:14px 0}a{color:#9dd4fc}#status{color:#ffd08a}@media(max-width:900px){main{grid-template-columns:1fr}}</style>
<header><h1>新闻事实 → 公司归属与传导 · 证据复核</h1>
<p>历史开发材料 · AI 弱标签 · 尚未人工核实 · 不作为自动删除或训练准入依据</p>
<p id="counts"></p><select id="pair" aria-label="新闻与目标公司"></select>
<select id="mode" aria-label="标注提示"><option value="primary">主提示</option><option value="audit">审查提示（同一提供商）</option></select>
<p id="status"></p></header>
<main><section><h2>新闻级事实</h2><a id="source" target="_blank" rel="noopener">打开原始来源</a>
<div id="facts"></div><details><summary>完整新闻证据与坐标</summary><pre id="news"></pre></details></section>
<section><h2>目标公司归属与传导</h2><div id="labels"></div>
<details><summary>完整公司业务证据</summary><pre id="company"></pre></details></section></main>
<script type="application/json" id="data">'''+data+'''</script>
<script>
const data=JSON.parse(document.getElementById('data').textContent);
const pair=document.getElementById('pair'),mode=document.getElementById('mode');
document.getElementById('counts').textContent=`${data.summary.articles} 篇新闻 / ${data.summary.pairs} 个配对；两提示均通过结构校验 ${data.summary.both_structurally_valid} 个；待补标 ${data.summary.pending_label} 个。结构校验不等于语义正确。`;
data.queue.forEach((q,i)=>{const o=document.createElement('option');o.value=i;o.textContent=q.company.company.name+' · '+q.title;pair.append(o)});
function block(parent,title,value){const h=document.createElement('h3');h.textContent=title;const p=document.createElement('pre');p.textContent=JSON.stringify(value,null,2);parent.append(h,p)}
function show(){const q=data.queue[Number(pair.value||0)];if(!q)return;const a=data.articles[q.article_id],f=a.results[mode.value],label=q.results[mode.value];
const facts=document.getElementById('facts'),labels=document.getElementById('labels');facts.replaceChildren();labels.replaceChildren();
document.getElementById('status').textContent=q.status+' · 所有材料保留；UNKNOWN、校验失败和缺结果均等待复核';
document.getElementById('news').textContent=JSON.stringify(a.packet,null,2);document.getElementById('company').textContent=JSON.stringify(q.company,null,2);
const source=document.getElementById('source');try{const u=new URL(q.source.url);if(!['https:','http:'].includes(u.protocol))throw Error();source.href=u.href;source.hidden=false}catch{source.removeAttribute('href');source.hidden=true}
if(f){block(facts,'新闻信号及抽取校验',{valid:f.evidence_valid,error:f.error,article_signal_presence:f.answer.article_signal_presence});for(const fact of f.answer.facts||[])block(facts,fact.fact_id+' · '+fact.scope,fact)}else block(facts,'待补事实',{status:'pending_extraction'});
if(label){block(labels,'归属及传导校验',{valid:label.evidence_valid,error:label.error,own_event:label.answer.own_event,own_fact_ids:label.answer.own_fact_ids});for(const link of label.answer.links||[])block(labels,link.fact_id+' · '+link.transmission,link);block(labels,'按原位置解析的业务证据',label.answer.resolved_business_evidence||[])}else block(labels,'待补标注',{status:'pending_label'});
}
pair.addEventListener('change',show);mode.addEventListener('change',show);show();
</script></html>'''


def export(root):
    root = Path(root); queue = read_jsonl(root/'review_queue.jsonl')
    records = {r['id']:r for r in read_jsonl(root/'inputs.jsonl')}
    facts = {mode:{x['id']:x for x in read_jsonl(root/'teacher'/('facts_'+mode+'.jsonl'))} for mode in ('primary','audit')}
    articles = {}
    for q in queue:
        r = records[q['id']]
        q.update(company=company_packet(r), title=r['state']['news']['title'], source=r['source'])
        articles[q['article_id']] = {'packet':article_packet(q['article_id'],r['state']['news']),
                                     'results':{mode:facts[mode].get(q['article_id']) for mode in facts}}
    path = root/'human_review.html'
    path.write_text(render(json.loads((root/'review_summary.json').read_text()),queue,articles))
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('root',type=Path)
    print(export(parser.parse_args().root))
