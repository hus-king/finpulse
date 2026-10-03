"""Morning digest preview, opt-in scheduling and configured delivery adapters."""
import asyncio
import html
import json
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .catalog import DEFAULT_WATCHLIST, stock_by_code
from .news_cleaning import SHANGHAI
from .providers import read_config


def build_briefing(store, owner):
    codes = store.get('watchlist', 'list', owner, DEFAULT_WATCHLIST)
    sections = []
    for code in codes:
        snapshot = store.get('dashboard', code, default={})
        stock = store.catalog.get(code) if hasattr(store, 'catalog') else stock_by_code(code)
        stock = stock or snapshot.get('stock') or {'name': code}
        news = snapshot.get('news', [])
        sections.append({'code': code, 'name': stock['name'], 'as_of': snapshot.get('as_of'), 'news': [{'id': row['id'], 'title': row['title'], 'url': row['url'], 'time': row['time'], 'score': row.get('score'), 'summary': (row.get('analysis') or {}).get('summary'), 'uncertainty': (row.get('analysis') or {}).get('uncertainty')} for row in news[:5]]})
    generated = datetime.now(SHANGHAI).isoformat()
    content = '<h1>FinPulse 自选股早报</h1><p>' + html.escape(generated) + '</p>'
    for section in sections:
        content += '<h2>' + html.escape(section['name']) + '</h2>'
        content += '<p>数据更新：' + html.escape(section['as_of'] or '尚未采集') + '</p><ul>'
        for row in section['news']:
            content += '<li><a href="' + html.escape(row['url'], quote=True) + '">' + html.escape(row['title']) + '</a><p>' + html.escape(row['summary'] or '尚未研判') + '</p><p>' + html.escape(row['uncertainty'] or '') + '</p></li>'
        content += '</ul>'
    content += '<p>有限来源的消息汇总，需回到原文核验；不构成投资建议。</p>'
    return {'generated_at': generated, 'sections': sections, 'html': content, 'note': '预览使用已保存的真实数据，不会额外调用模型或发送消息。'}


def smtp_send(config, recipient, title, content):
    message = EmailMessage()
    message['Subject'], message['From'], message['To'] = title, config['from_email'], recipient
    message.set_content('FinPulse 自选股早报，请使用支持 HTML 的邮件客户端查看。')
    message.add_alternative(content, subtype='html')
    port = int(config.get('port', 465))
    context = ssl.create_default_context()
    if port == 465:
        client = smtplib.SMTP_SSL(config['host'], port, timeout=20, context=context)
    else:
        client = smtplib.SMTP(config['host'], port, timeout=20)
    with client:
        if port != 465:
            client.starttls(context=context)
        client.login(config['username'], config['password'])
        client.send_message(message)


async def deliver(subscription, digest):
    config = read_config().get('notifications', {})
    outcomes = {}
    if subscription.get('email'):
        if not config.get('smtp', {}).get('host'):
            outcomes['email'] = 'not_configured'
        else:
            try:
                await asyncio.to_thread(smtp_send, config['smtp'], subscription['email'], 'FinPulse 自选股早报', digest['html'])
                outcomes['email'] = 'sent'
            except Exception:
                outcomes['email'] = 'failed'
    if subscription.get('pushplus_token'):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post('https://www.pushplus.plus/send', json={'token': subscription['pushplus_token'], 'title': 'FinPulse 自选股早报', 'content': digest['html'], 'template': 'html'})
            outcomes['wechat'] = 'accepted' if response.is_success and response.json().get('code') == 200 else 'failed'
        except (httpx.RequestError, ValueError):
            outcomes['wechat'] = 'failed'
    return outcomes


async def morning_run(service):
    # One scheduler instance per shared DB; other local developer instances leave it disabled.
    for row in service.store.list('subscription'):
        owner, subscription = row['owner'], row['value']
        if not subscription.get('enabled'):
            continue
        with service.store.db.connection() as conn:
            account = conn.execute('SELECT is_active FROM users WHERE id=?', (owner,)).fetchone()
        if not account or not account['is_active']:
            continue
        try:
            for code in service.store.get('watchlist', 'list', owner, DEFAULT_WATCHLIST):
                async with service.capacity:
                    async with service.locks.setdefault(code, asyncio.Lock()):
                        await service.collect(code, 7, 3, True)
            digest = build_briefing(service.store, owner)
            service.store.put('briefing', 'latest', digest, owner)
            outcomes = await deliver(subscription, digest)
            service.store.put('delivery', 'latest', {'at': datetime.now(SHANGHAI).isoformat(), 'status': outcomes}, owner)
        except Exception as exc:
            service.store.put('delivery', 'latest', {'at': datetime.now(SHANGHAI).isoformat(), 'status': {'job': 'failed'}, 'error_type': type(exc).__name__}, owner)


def start_scheduler(service):
    settings = read_config().get('scheduler', {})
    if not settings.get('enabled', False):
        return None
    hour, minute = map(int, settings.get('morning_time', '08:30').split(':'))
    scheduler = AsyncIOScheduler(timezone='Asia/Shanghai')
    scheduler.add_job(morning_run, 'cron', args=[service], day_of_week='mon-fri', hour=hour, minute=minute, max_instances=1, coalesce=True, misfire_grace_time=1800, id='morning_digest')
    scheduler.start()
    return scheduler
