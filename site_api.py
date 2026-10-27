"""فراخوانی API داخلی سایت برای رسید و جم با اطلاعات سایت."""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

SITE_API_URL = (os.getenv('SITE_API_URL') or 'https://atomicshop.ir').strip().rstrip('/')
BOT_INTERNAL_SECRET = (os.getenv('BOT_INTERNAL_SECRET') or '').strip()

_EXC_TYPE = re.compile(
    r'Exception Type:\s*</th>\s*<td[^>]*>\s*([^<]+)',
    re.I,
)
_TITLE = re.compile(r'<title>([^<]+)</title>', re.I)


def _error_from_body(raw: str, status: int) -> dict:
    try:
        data = json.loads(raw) if raw else {}
        if isinstance(data, dict):
            data.setdefault('ok', False)
            data.setdefault('error', f'http_{status}')
            return data
    except (ValueError, TypeError):
        pass
    hint = ''
    match = _EXC_TYPE.search(raw or '')
    if match:
        hint = match.group(1).strip()[:80]
    else:
        match = _TITLE.search(raw or '')
        if match:
            hint = match.group(1).strip()[:80]
    return {
        'ok': False,
        'error': hint or f'http_{status}',
        'message': hint or f'http_{status}',
    }


def _site_request(path, *, method='GET', payload=None, query=None, timeout=25):
    secret = (os.getenv('BOT_INTERNAL_SECRET') or BOT_INTERNAL_SECRET or '').strip()
    base = (os.getenv('SITE_API_URL') or SITE_API_URL or 'https://atomicshop.ir').strip().rstrip('/')
    if not secret:
        return {'ok': False, 'error': 'missing_secret'}
    url = f'{base}{path}'
    if query:
        url = f'{url}?{urllib.parse.urlencode(query)}'
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    headers = {'X-Bot-Secret': secret, 'User-Agent': 'AtomicBot/1.0'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw) if raw else {'ok': False, 'error': 'empty'}
    except urllib.error.HTTPError as exc:
        raw = ''
        try:
            raw = exc.read().decode('utf-8', errors='replace')
        except Exception:
            raw = ''
        return _error_from_body(raw, exc.code)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log.warning('Site API failed %s %s: %s', method, path, exc)
        return {'ok': False, 'error': str(exc)}


def call_site_review(payment_id, action, admin_tg, admin_name):
    """POST به سایت. نتیجه dict است؛ در خطا {'ok': False, 'error': ...}."""
    return _site_request(
        f'/internal/bot/card-transfer/{int(payment_id)}/review/',
        method='POST',
        payload={
            'action': action,
            'admin_tg_id': str(admin_tg or ''),
            'admin_name': str(admin_name or ''),
        },
    )


def fetch_site_ops():
    data = _site_request('/internal/bot/site-ops/')
    if not data.get('ok'):
        return {
            'ok': False,
            'error': data.get('error') or 'site_ops',
            'ready_credentials': 0,
            'pending_receipts': 0,
        }
    return {
        'ok': True,
        'ready_credentials': int(data.get('ready_credentials') or 0),
        'pending_receipts': int(data.get('pending_receipts') or 0),
    }


def fetch_site_credentials(*, paid_only=False):
    query = {'paid_only': '1'} if paid_only else None
    data = _site_request('/internal/bot/site-credentials/', query=query)
    if not data.get('ok'):
        data.setdefault('items', [])
    return data


def fetch_site_credential(pk):
    return _site_request(f'/internal/bot/site-credentials/{int(pk)}/')


def call_site_credential_action(pk, action, admin_tg, admin_name):
    return _site_request(
        f'/internal/bot/site-credentials/{int(pk)}/action/',
        method='POST',
        payload={
            'action': action,
            'admin_tg_id': str(admin_tg or ''),
            'admin_name': str(admin_name or ''),
        },
    )


def fetch_site_receipts():
    data = _site_request('/internal/bot/site-receipts/')
    if not data.get('ok'):
        data.setdefault('items', [])
    return data


def fetch_site_receipt(pk):
    return _site_request(f'/internal/bot/site-receipts/{int(pk)}/')


# ── صف اعلان‌های سایت ────────────────────────────────────────────────
# سرور سایت در ایران است و به api.telegram.org نمی‌رسد. رسید، احراز کارت و
# سفارش جمِ پرداخت‌شده در صفِ سایت می‌مانند و ربات (این‌جا، خارج از ایران)
# برشان می‌دارد، می‌فرستد و تحویل را تایید می‌کند.


def fetch_site_outbox(limit=10):
    data = _site_request('/internal/bot/outbox/', query={'limit': int(limit)}, timeout=20)
    if not data.get('ok'):
        data.setdefault('items', [])
    return data


def ack_site_outbox(item_id, notices):
    return _site_request(
        f'/internal/bot/outbox/{int(item_id)}/ack/',
        method='POST',
        payload={'notices': list(notices or [])},
        timeout=20,
    )


def fetch_site_photo(url, *, max_bytes=10 * 1024 * 1024, timeout=25):
    """بایت‌های عکسِ امضاشده از سایت؛ تلگرام خودش به سرور ایران نمی‌رسد."""
    if not url or not str(url).startswith(('https://', 'http://')):
        return None
    req = urllib.request.Request(url, headers={'User-Agent': 'AtomicBot/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log.warning('Site photo download failed: %s', exc)
        return None
    if len(data) > max_bytes:
        log.warning('Site photo too large; sending without it')
        return None
    return data


def call_site_card_review(card_id, action, admin_tg, admin_name):
    """تایید/رد احراز کارت بانکیِ سایت."""
    return _site_request(
        f'/internal/bot/card-verify/{int(card_id)}/review/',
        method='POST',
        payload={
            'action': action,
            'admin_tg_id': str(admin_tg or ''),
            'admin_name': str(admin_name or ''),
        },
    )


def fetch_site_order(code, telegram_id):
    """وضعیتِ سفارش سایت با کد پیگیری (AS…)."""
    return _site_request(
        '/internal/bot/order-track/',
        query={'code': str(code or '').strip().upper(), 'tg': str(telegram_id or '')},
        timeout=20,
    )


def fetch_site_ping():
    """وضعیت صف‌ها و بک‌لاگ بررسی‌ها در سایت."""
    return _site_request('/internal/bot/ping/', timeout=15)


def fetch_site_verified_cards(phone, telegram_id):
    """کارت‌های تاییدشدهٔ حسابِ سایت با همین شماره (احراز مشترک)."""
    data = _site_request('/internal/bot/verified-cards/',
                         query={'phone': str(phone or ''), 'tg': str(telegram_id or '')}, timeout=15)
    if not data.get('ok'):
        data.setdefault('cards', [])
    return data
