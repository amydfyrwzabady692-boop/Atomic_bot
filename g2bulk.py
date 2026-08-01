"""کلاینت G2Bulk — تایید آیدی و تحویل خودکار جم فری‌فایر (Middle East)."""
import json
import hashlib
import hmac
import os
import re
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

BASE_URL = 'https://api.g2bulk.com/v1'
G2BULK_ME_AMOUNTS = (110, 231, 583, 1188, 2420)
G2BULK_ME_CATALOGUE_NAMES = (
    'Level Up Package - Level 6',
    'Level Up Package - Level 10',
    'Level Up Package - Level 15',
    'Level Up Package - Level 20',
    'Level Up Package - Level 25',
    'Level Up Package - Level 30',
    '110',
    '231',
    'Weekly Membership',
    'Booyah Pass',
    '583',
    '1188',
    'Monthly Membership',
    '2420',
)
_inventory_cache = {'at': 0.0, 'value': None}


def _api_key():
    return (os.getenv('G2BULK_API_KEY') or '').strip()


def _game_code():
    return (os.getenv('G2BULK_GAME_CODE') or 'freefire_me').strip()


def is_configured():
    return bool(_api_key())


def is_supported_amount(amount):
    return int(amount) in G2BULK_ME_AMOUNTS


def _normalise_catalogue_name(name):
    return ' '.join(str(name or '').strip().casefold().split())


def is_supported_catalogue(amount, catalogue_name=''):
    """Only allow products explicitly approved for the Free Fire ME catalogue."""
    normalised = _normalise_catalogue_name(catalogue_name)
    if normalised:
        return normalised in {
            _normalise_catalogue_name(name)
            for name in G2BULK_ME_CATALOGUE_NAMES
        }
    try:
        return is_supported_amount(amount)
    except (TypeError, ValueError):
        return False


def _request(method, path, body=None, idempotency_key=None):
    url = f'{BASE_URL}{path}'
    headers = {
        'Accept': 'application/json',
        'X-API-Key': _api_key(),
    }
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    if idempotency_key:
        headers['X-Idempotency-Key'] = idempotency_key

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        try:
            result = json.loads(raw)
            if isinstance(result, dict):
                result.setdefault('_http_status', e.code)
                if method.upper() != 'GET' and (e.code in (408, 429) or e.code >= 500):
                    result['_transport_uncertain'] = True
                return result
            return {'success': False, 'message': str(result), '_http_status': e.code}
        except ValueError:
            return {
                'success': False, 'message': raw or str(e),
                '_http_status': e.code,
            }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {
            'success': False,
            'message': str(e),
            '_transport_uncertain': method.upper() != 'GET',
        }


def check_player_id(user_id):
    """تایید آیدی فری‌فایر. خروجی: {ok, name, error}"""
    if not is_configured():
        return {'ok': False, 'error': 'سرویس تایید آیدی پیکربندی نشده است.'}

    body = {'game': _game_code(), 'user_id': str(user_id).strip()}
    data = _request('POST', '/games/checkPlayerId', body)
    if data.get('valid') == 'valid' and data.get('name'):
        return {'ok': True, 'name': data['name']}
    return {
        'ok': False,
        'error': data.get('message') or 'آیدی بازی معتبر نیست. لطفاً دوباره بررسی کنید.',
    }


def get_inventory_snapshot(force=False):
    """موجودی دلاری و قیمت زنده کاتالوگ بازی را با کش کوتاه برمی‌گرداند."""
    if not is_configured():
        return {'ok': False, 'error': 'API key not configured'}
    now = time.monotonic()
    cached = _inventory_cache.get('value')
    # خطای احراز/شبکه را حتی در حالت force کوتاه‌مدت cache کن؛ طبق مستند API،
    # تکرار 401 می‌تواند IP را مسدود کند.
    if cached and not cached.get('ok') and now - _inventory_cache.get('at', 0) < 60:
        return cached
    if not force and cached and now - _inventory_cache.get('at', 0) < 45:
        return cached

    me = _request('GET', '/getMe')
    if not me.get('success') or me.get('balance') is None:
        result = {
            'ok': False,
            'error': me.get('message') or 'دریافت موجودی G2Bulk ناموفق بود.',
        }
        _inventory_cache.update(at=now, value=result)
        return result
    catalogue = _request('GET', f'/games/{_game_code()}/catalogue')
    if not catalogue.get('success'):
        result = {
            'ok': False,
            'error': catalogue.get('message') or 'دریافت کاتالوگ G2Bulk ناموفق بود.',
        }
        _inventory_cache.update(at=now, value=result)
        return result
    try:
        balance = Decimal(str(me['balance']))
    except (InvalidOperation, TypeError, ValueError):
        return {'ok': False, 'error': 'موجودی برگشتی G2Bulk معتبر نیست.'}

    prices = {}
    names = {}
    prices_by_name = {}
    for item in catalogue.get('catalogues') or []:
        name = str(item.get('name') or '').strip()
        match = re.search(r'\d+', name)
        try:
            package_amount = int(match.group()) if match else None
            cost = Decimal(str(item.get('amount')))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not name or cost <= 0:
            continue
        prices_by_name[_normalise_catalogue_name(name)] = cost
        if package_amount:
            prices[package_amount] = cost
            names[package_amount] = name
    result = {
        'ok': True,
        'balance': balance,
        'currency': str(me.get('currency') or 'USD'),
        'prices': prices,
        'prices_by_name': prices_by_name,
        'names': names,
        'username': me.get('username') or '',
    }
    _inventory_cache.update(at=now, value=result)
    return result


def can_fulfill(amount, catalogue_name='', force=False):
    """بررسی می‌کند یک بسته با موجودی فعلی حساب API قابل سفارش است."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return False, None, None, 'مقدار بسته نامعتبر است.'
    snapshot = get_inventory_snapshot(force=force)
    if not snapshot.get('ok'):
        return False, None, None, snapshot.get('error')
    cost = None
    if catalogue_name:
        cost = snapshot.get('prices_by_name', {}).get(
            _normalise_catalogue_name(catalogue_name)
        )
    if cost is None:
        cost = snapshot.get('prices', {}).get(amount)
    if cost is None and catalogue_name:
        match = re.search(r'\d+', str(catalogue_name))
        if match:
            cost = snapshot.get('prices', {}).get(int(match.group()))
    if cost is None:
        return False, None, snapshot['balance'], 'بسته در کاتالوگ زنده API پیدا نشد.'
    available = snapshot['balance'] + 1e-9 >= cost
    return available, cost, snapshot['balance'], (
        None if available else 'موجودی دلاری API برای این بسته کافی نیست.'
    )


def build_callback_url(order_pk, gem_info_pk):
    """Build an unguessable HTTPS callback URL bound to one local item."""
    base = (os.getenv('PAYMENT_CALLBACK_BASE') or '').strip().rstrip('/')
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != 'https' or not parsed.hostname or not _api_key():
        return ''
    message = f'{int(order_pk)}:{int(gem_info_pk)}'.encode('utf-8')
    token = hmac.new(
        _api_key().encode('utf-8'), message, hashlib.sha256
    ).hexdigest()
    query = urllib.parse.urlencode({
        'order': int(order_pk),
        'item': int(gem_info_pk),
        'token': token,
    })
    return f'{base}/g2bulk/callback?{query}'


def verify_callback_token(order_pk, gem_info_pk, token):
    if not _api_key() or not str(token or '').strip():
        return False
    try:
        message = f'{int(order_pk)}:{int(gem_info_pk)}'.encode('utf-8')
    except (TypeError, ValueError):
        return False
    expected = hmac.new(
        _api_key().encode('utf-8'), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, str(token).strip())


def place_game_order(
    catalogue_name, player_id, remark='', idempotency_key=None,
    callback_url='',
):
    """ثبت سفارش شارژ. خروجی: {ok, order_id, status, player_name, error}"""
    if not is_configured():
        return {'ok': False, 'error': 'API key not configured'}

    body = {
        'catalogue_name': str(catalogue_name),
        'player_id': str(player_id).strip(),
    }
    if remark:
        body['remark'] = remark
    if callback_url:
        parsed_callback = urllib.parse.urlparse(str(callback_url))
        if parsed_callback.scheme != 'https' or not parsed_callback.hostname:
            return {'ok': False, 'error': 'G2Bulk callback URL is invalid.'}
        body['callback_url'] = str(callback_url)

    data = _request(
        'POST',
        f'/games/{_game_code()}/order',
        body,
        idempotency_key=idempotency_key,
    )
    if data.get('success') and data.get('order'):
        order = data['order']
        provider_order_id = order.get('order_id')
        if not provider_order_id:
            return {
                'ok': False,
                'uncertain': True,
                'error': 'سفارش پذیرفته شد اما شناسه تأمین‌کننده برنگشت.',
            }
        return {
            'ok': True,
            'order_id': provider_order_id,
            'status': order.get('status', 'PENDING'),
            'player_name': order.get('player_name', ''),
            'cost_usd': order.get('price'),
        }
    return {
        'ok': False,
        'uncertain': bool(data.get('_transport_uncertain')),
        'error': data.get('message') or 'ثبت سفارش در G2Bulk ناموفق بود.',
    }


def find_game_order_by_remark(remark):
    """Recover an ambiguously submitted order without creating a second order."""
    remark = str(remark or '').strip()
    if not is_configured() or not remark:
        return {'ok': False, 'found': False}
    data = _request('GET', '/games/orders?page=1&limit=100')
    if not data.get('success'):
        return {
            'ok': False,
            'found': False,
            'error': data.get('message') or 'دریافت سفارش‌های G2Bulk ناموفق بود.',
        }
    nested = data.get('data') if isinstance(data.get('data'), dict) else {}
    orders = data.get('orders') or nested.get('orders') or []
    for order in orders:
        if str(order.get('remark') or '').strip() != remark:
            continue
        provider_order_id = order.get('order_id') or order.get('id')
        if not provider_order_id:
            continue
        status = str(order.get('status') or 'PENDING').strip().upper()
        if status == 'CANCELED':
            status = 'FAILED'
        return {
            'ok': True,
            'found': True,
            'order_id': provider_order_id,
            'status': status,
            'player_name': order.get('player_name') or '',
            'cost_usd': order.get('price') or order.get('total_price'),
        }
    return {'ok': True, 'found': False}


def get_game_order_status(order_id):
    """وضعیت زنده یک سفارش شارژ مستقیم را دریافت می‌کند."""
    if not is_configured() or not str(order_id or '').strip():
        return {'ok': False, 'error': 'شناسه سفارش یا API key موجود نیست.'}
    provider_id = str(order_id).strip()
    provider_value = int(provider_id) if provider_id.isdigit() else provider_id
    data = _request(
        'POST', '/games/order/status', {'order_id': provider_value}
    )
    def extract_status(response):
        nested = (
            response.get('data')
            if isinstance(response.get('data'), dict) else {}
        )
        order = (
            response.get('order')
            if isinstance(response.get('order'), dict) else {}
        )
        if not order and isinstance(nested.get('order'), dict):
            order = nested.get('order')
        if not order and nested.get('status'):
            order = nested
        status = str(
            order.get('status')
            or nested.get('status')
            or response.get('status')
            or ''
        ).strip().upper()
        return status, order, nested

    status, order, nested = extract_status(data)
    if (
        status not in {
            'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED',
            'CANCELED', 'CANCELLED', 'REFUNDED',
        }
        and provider_id.isdigit()
    ):
        # Older deployments have disagreed on whether order_id is a JSON
        # number or string. Retrying this read-only endpoint cannot charge.
        alternate = _request(
            'POST', '/games/order/status', {'order_id': provider_id}
        )
        alternate_status, alternate_order, alternate_nested = extract_status(
            alternate
        )
        if alternate_status:
            data = alternate
            status = alternate_status
            order = alternate_order
            nested = alternate_nested
    if status in {
        'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED',
        'CANCELED', 'CANCELLED', 'REFUNDED',
    }:
        return {
            'ok': True,
            'status': (
                'FAILED'
                if status in {'CANCELED', 'CANCELLED', 'REFUNDED'}
                else status
            ),
            'player_name': (
                order.get('player_name')
                or nested.get('player_name')
                or data.get('player_name')
                or ''
            ),
        }
    # Some deployments have returned a non-standard body from the dedicated
    # status endpoint. Reconcile against order history before leaving a paid
    # order stuck in PROCESSING. This is read-only and can never create an order.
    # The documented game-order history endpoint has no `search` parameter.
    # Read its newest page and match the persisted provider id locally.
    history = _request('GET', '/games/orders?page=1&limit=100')
    history_nested = (
        history.get('data') if isinstance(history.get('data'), dict) else {}
    )
    history_orders = (
        history.get('orders')
        or history_nested.get('orders')
        or (history.get('data') if isinstance(history.get('data'), list) else [])
        or []
    )
    for item in history_orders:
        provider_id = str(item.get('order_id') or item.get('id') or '').strip()
        if provider_id != str(order_id).strip():
            continue
        history_status = str(item.get('status') or '').strip().upper()
        if history_status in {
            'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'CANCELED'
        }:
            return {
                'ok': True,
                'status': (
                    'FAILED' if history_status == 'CANCELED' else history_status
                ),
                'player_name': item.get('player_name') or '',
            }
    return {
        'ok': False,
        'error': data.get('message') or 'وضعیت سفارش G2Bulk قابل تشخیص نیست.',
    }


def get_game_order_details(order_id):
    """Read one provider order for safe manual reconciliation; never submits."""
    provider_id = str(order_id or '').strip()
    if not is_configured() or not provider_id:
        return {'ok': False, 'error': 'شناسه سفارش یا API key موجود نیست.'}
    data = _request('GET', '/games/orders?page=1&limit=100')
    nested = data.get('data') if isinstance(data.get('data'), dict) else {}
    orders = (
        data.get('orders')
        or nested.get('orders')
        or (data.get('data') if isinstance(data.get('data'), list) else [])
        or []
    )
    for item in orders:
        item_id = str(item.get('order_id') or item.get('id') or '').strip()
        if item_id != provider_id:
            continue
        status = str(item.get('status') or '').strip().upper()
        if status == 'CANCELED':
            status = 'FAILED'
        return {
            'ok': True,
            'order_id': provider_id,
            'status': status,
            'player_id': str(item.get('player_id') or '').strip(),
            'player_name': item.get('player_name') or '',
            'cost_usd': item.get('price') or item.get('total_price'),
        }
    return {
        'ok': False,
        'error': data.get('message') or 'سفارش در سابقه G2Bulk پیدا نشد.',
    }


def idempotency_key(order_pk, gem_info_pk):
    return str(uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f'atomicbot-order-{order_pk}-gem-{gem_info_pk}',
    ))
