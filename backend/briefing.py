"""Morning digest preview, opt-in scheduling and configured delivery adapters."""
import asyncio
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.headerregistry import Address

import httpx
from .providers import read_config


def build_briefing(store, owner):
    from .recommendations import build_digest
    return build_digest(store, owner)


def smtp_send(config, recipient, title, content):
    message = EmailMessage()
    message['Subject'], message['To'] = title, recipient
    message['From'] = Address(display_name=config.get('from_name', '').strip(), addr_spec=config['from_email'])
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
        client.send_message(message, from_addr=config['from_email'], to_addrs=[recipient])


async def deliver(subscription, digest):
    config = read_config().get('notifications', {})
    outcomes = {}
    if subscription.get('email') and subscription.get('email_enabled', True):
        if not all(config.get('smtp', {}).get(key) for key in ('host', 'username', 'password', 'from_email')):
            outcomes['email'] = 'not_configured'
        else:
            try:
                await asyncio.to_thread(smtp_send, config['smtp'], subscription['email'], 'FinPulse 自选股早报', digest['html'])
                outcomes['email'] = 'sent'
            except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused):
                outcomes['email'] = 'failed'
            except Exception:
                outcomes['email'] = 'unknown'
    if subscription.get('pushplus_token') and subscription.get('wechat_enabled', True):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post('https://www.pushplus.plus/send', json={'token': subscription['pushplus_token'], 'title': 'FinPulse 自选股早报', 'content': digest['html'], 'template': 'html'})
            outcomes['wechat'] = 'accepted' if response.is_success and response.json().get('code') == 200 else 'unknown' if response.status_code >= 500 else 'failed'
        except (httpx.RequestError, ValueError):
            outcomes['wechat'] = 'unknown'
    return outcomes


async def morning_run(service):
    await service.briefing.run()


def start_scheduler(service):
    return service.briefing.start_scheduler()
