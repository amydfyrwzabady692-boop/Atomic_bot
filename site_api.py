"""فراخوانی API داخلی سایت برای تایید/رد رسید کارت‌به‌کارت."""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
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


def call_site_review(payment_id, action, admin_tg, admin_name):
    """POST به سایت. نتیجه dict است؛ در خطا {'ok': False, 'error': ...}."""
    secret = (os.getenv('BOT_INTERNAL_SECRET') or BOT_INTERNAL_SECRET or '').strip()
    base = (os.getenv('SITE_API_URL') or SITE_API_URL or 'https://atomicshop.ir').strip().rstrip('/')
    if not secret:
        return {'ok': False, 'error': 'missing_secret'}
    url = f'{base}/internal/bot/card-transfer/{int(payment_id)}/review/'
    body = json.dumps({
        'action': action,
        'admin_tg_id': str(admin_tg or ''),
        'admin_name': str(admin_name or ''),
    }).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'X-Bot-Secret': secret,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
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
        log.warning('Site review API failed payment=%s: %s', payment_id, exc)
        return {'ok': False, 'error': str(exc)}
