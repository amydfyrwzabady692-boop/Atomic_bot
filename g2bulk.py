"""کلاینت G2Bulk — تایید آیدی و تحویل خودکار جم فری‌فایر (Middle East)."""
import json
import hashlib
import hmac
import os
import re
import threading
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
_inventory_refresh_lock = threading.Lock()
_INVENTORY_CACHE_SECONDS = 5 * 60
_FORCED_REFRESH_COALESCE_SECONDS = 30
_products_cache = {'at': 0.0, 'value': None}
_PRODUCTS_CACHE_SECONDS = 6 * 60 * 60
TELEGRAM_GAME_CODE = 'Telegram'
# بسته‌های ۱M استارز قیمت‌شان از INTEGER دیتابیس رد می‌شود و کل سینک را می‌ترکاند.
MAX_STARS_FOR_SALE = 100_000
_stars_cache = {'at': 0.0, 'value': None}
_stars_refresh_lock = threading.Lock()
_STARS_CACHE_SECONDS = 5 * 60


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
    }
    if _api_key():
        headers['X-API-Key'] = _api_key()
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


def check_player_id(user_id, game=None):
    """تایید آیدی بازیکن. خروجی: {ok, name, error}"""
    if not is_configured():
        return {'ok': False, 'error': 'سرویس تایید آیدی پیکربندی نشده است.'}

    code = str(game or _game_code()).strip() or _game_code()
    player = str(user_id or '').strip()
    body = {'game': code, 'user_id': player, 'userid': player}
    data = _request('POST', '/games/checkPlayerId', body)
    if data.get('valid') == 'valid' and data.get('name'):
        return {'ok': True, 'name': data['name']}
    if data.get('valid') == 'valid':
        return {'ok': True, 'name': player}
    return {
        'ok': False,
        'error': data.get('message') or 'آیدی معتبر نیست. لطفاً دوباره بررسی کنید.',
    }


def parse_stars_amount(name):
    """«50 Stars» / «1.5K Stars» / «1M Stars» → عدد استارز. پرمیوم None."""
    text = str(name or '').strip()
    if not text or 'premium' in text.casefold():
        return None
    match = re.search(
        r'([\d]+(?:\.\d+)?)\s*([kKmM])?\s*stars?\b',
        text, re.I,
    )
    if not match:
        return None
    try:
        value = Decimal(match.group(1))
    except (InvalidOperation, TypeError, ValueError):
        return None
    suffix = (match.group(2) or '').upper()
    if suffix == 'K':
        value *= Decimal(1000)
    elif suffix == 'M':
        value *= Decimal(1_000_000)
    amount = int(value)
    return amount if amount > 0 else None


def stars_amount_for_sale(amount):
    try:
        value = int(amount)
    except (TypeError, ValueError):
        return False
    return 0 < value <= MAX_STARS_FOR_SALE


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
    if (
        not force and cached
        and now - _inventory_cache.get('at', 0) < _INVENTORY_CACHE_SECONDS
    ):
        return cached

    # Coalesce concurrent refreshes.  Without this lock, several users
    # opening products together can each trigger the same two supplier calls
    # and make the whole bot feel stuck.  A forced financial check only reuses
    # a snapshot another request completed in the last two seconds.
    with _inventory_refresh_lock:
        now = time.monotonic()
        cached = _inventory_cache.get('value')
        age = now - _inventory_cache.get('at', 0)
        if cached and (
            (not force and age < _INVENTORY_CACHE_SECONDS)
            or (force and cached.get('ok') and age < _FORCED_REFRESH_COALESCE_SECONDS)
            or (not cached.get('ok') and age < 60)
        ):
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
            result = {'ok': False, 'error': 'موجودی برگشتی G2Bulk معتبر نیست.'}
            _inventory_cache.update(at=now, value=result)
            return result

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


def get_itunes_turkey_costs(force=False):
    """Return live USD supplier cost for 60/300 TRY Apple credit.

    G2Bulk currently carries an exact 300 TRY denomination but not 60 TRY.
    The weekly 60 TRY cost is therefore derived from the same live 300 TRY
    product's per-lira cost.  No voucher is purchased by this lookup.
    """
    now = time.monotonic()
    cached = _products_cache.get('value')
    if cached and not force and now - _products_cache.get('at', 0) < _PRODUCTS_CACHE_SECONDS:
        return cached
    data = _request('GET', '/products')
    if not data.get('success'):
        result = {
            'ok': False,
            'error': data.get('message') or 'دریافت محصولات G2Bulk ناموفق بود.',
        }
        _products_cache.update(at=now, value=result)
        return result
    exact = None
    candidates = []
    for item in data.get('products') or []:
        title = _normalise_catalogue_name(item.get('title'))
        category = _normalise_catalogue_name(item.get('category_title'))
        if 'itunes turkey' not in f'{title} {category}':
            continue
        match = re.search(r'\b(\d+)\s*(?:try|tl)\b', title)
        if not match:
            continue
        try:
            denomination = int(match.group(1))
            unit_price = Decimal(str(item.get('unit_price')))
            stock = int(item.get('stock') or 0)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if denomination <= 0 or unit_price <= 0:
            continue
        candidate = (denomination, unit_price, stock, int(item.get('id')))
        candidates.append(candidate)
        if denomination == 300:
            exact = candidate
    chosen = exact or (min(candidates, key=lambda row: abs(row[0] - 300)) if candidates else None)
    if not chosen:
        result = {'ok': False, 'error': 'گیفت کارت iTunes Turkey در کاتالوگ پیدا نشد.'}
    else:
        denomination, unit_price, stock, product_id = chosen
        per_try = unit_price / Decimal(denomination)
        result = {
            'ok': True,
            'costs': {
                60: (per_try * Decimal(60)).quantize(Decimal('0.000001')),
                300: (per_try * Decimal(300)).quantize(Decimal('0.000001')),
            },
            'source_product_id': product_id,
            'source_denomination_try': denomination,
            'source_unit_price_usd': unit_price,
            'stock': stock,
        }
    _products_cache.update(at=now, value=result)
    return result


GIFT_CARD_BRANDS = {
    'gplay_us': {
        'title': 'گوگل‌پلی آمریکا',
        'category_ids': {86},
        'category_names': ('google play usa',),
    },
    'itunes_us': {
        'title': 'آیتونز آمریکا',
        'category_ids': {3},
        'category_names': ('itunes usa',),
    },
    'itunes_tr': {
        'title': 'آیتونز ترکیه',
        'category_ids': {95},
        'category_names': ('apple itunes turkey', 'itunes turkey'),
    },
    'gplay_tr': {
        'title': 'گوگل‌پلی ترکیه',
        'category_ids': {83},
        'category_names': ('google play turkey',),
    },
}
GIFT_CARD_BRAND_ORDER = ('gplay_us', 'itunes_us', 'itunes_tr', 'gplay_tr')
_gift_catalog_cache = {'at': 0.0, 'value': None}
_GIFT_CATALOG_CACHE_SECONDS = 90


def gift_card_brand_title(brand):
    info = GIFT_CARD_BRANDS.get(str(brand or '').strip())
    return (info or {}).get('title') or str(brand or '')


def _parse_gift_face(title):
    text = str(title or '')
    match = re.search(r'(\d+)\s*TRY\b', text, re.I)
    if match:
        amount = int(match.group(1))
        return amount, 'TRY', f'{amount:,} لیر'
    match = re.search(r'(\d+)\s*USD\b', text, re.I)
    if match:
        amount = int(match.group(1))
        return amount, 'USD', f'{amount:,} دلار'
    match = re.search(r'(\d+)\s*\$', text)
    if match:
        amount = int(match.group(1))
        return amount, 'USD', f'{amount:,} دلار'
    return None, '', text.strip()


def _gift_brand_for_product(item):
    try:
        category_id = int(item.get('category_id') or 0)
    except (TypeError, ValueError):
        category_id = 0
    category = _normalise_catalogue_name(item.get('category_title'))
    for brand, info in GIFT_CARD_BRANDS.items():
        if category_id in info['category_ids']:
            return brand
        if category in info['category_names']:
            return brand
    return None


def _gift_in_stock(stock):
    try:
        value = int(stock)
    except (TypeError, ValueError):
        return False
    return value != 0


def list_gift_card_products(force=False):
    """کاتالوگ زنده چهار برند گیفت‌کارت از G2Bulk."""
    now = time.monotonic()
    cached = _gift_catalog_cache.get('value')
    if (
        cached and not force
        and now - _gift_catalog_cache.get('at', 0) < _GIFT_CATALOG_CACHE_SECONDS
    ):
        return cached
    data = _request('GET', '/products')
    if not data.get('success'):
        result = {
            'ok': False,
            'error': data.get('message') or 'دریافت گیفت‌کارت G2Bulk ناموفق بود.',
            'items': [],
            'by_id': {},
        }
        _gift_catalog_cache.update(at=now, value=result)
        return result
    items = []
    by_id = {}
    for item in data.get('products') or []:
        brand = _gift_brand_for_product(item)
        if not brand:
            continue
        try:
            product_id = int(item.get('id'))
            unit_price = Decimal(str(item.get('unit_price')))
            stock = int(item.get('stock') if item.get('stock') is not None else 0)
        except (InvalidOperation, TypeError, ValueError):
            continue
        if product_id <= 0 or unit_price <= 0:
            continue
        face_amount, face_currency, face_label = _parse_gift_face(item.get('title'))
        row = {
            'id': product_id,
            'brand': brand,
            'brand_title': gift_card_brand_title(brand),
            'title': str(item.get('title') or '').strip(),
            'unit_price': unit_price,
            'stock': stock,
            'in_stock': _gift_in_stock(stock),
            'face_amount': face_amount,
            'face_currency': face_currency,
            'face_label': face_label or str(item.get('title') or '').strip(),
        }
        items.append(row)
        by_id[product_id] = row
    items.sort(key=lambda row: (
        GIFT_CARD_BRAND_ORDER.index(row['brand'])
        if row['brand'] in GIFT_CARD_BRAND_ORDER else 99,
        row['face_amount'] or 10_000,
        row['unit_price'],
    ))
    result = {'ok': True, 'items': items, 'by_id': by_id}
    _gift_catalog_cache.update(at=now, value=result)
    return result


def get_gift_card_product(product_id, force=False):
    catalog = list_gift_card_products(force=force)
    if not catalog.get('ok'):
        return {'ok': False, 'error': catalog.get('error') or 'کاتالوگ در دسترس نیست.'}
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'شناسه گیفت‌کارت نامعتبر است.'}
    row = (catalog.get('by_id') or {}).get(product_id)
    if not row:
        return {'ok': False, 'error': 'این گیفت‌کارت در کاتالوگ G2Bulk نیست.'}
    return {'ok': True, **row}


def can_fulfill_gift_card(product_id, force=False):
    """موجودی انبار و موجودی دلاری G2Bulk را برای یک گیفت‌کارت چک می‌کند."""
    product = get_gift_card_product(product_id, force=force)
    if not product.get('ok'):
        return False, None, None, product.get('error')
    if not product.get('in_stock'):
        return False, product['unit_price'], None, 'موجودی این گیفت‌کارت تمام شده است.'
    snapshot = get_inventory_snapshot(force=force)
    if not snapshot.get('ok'):
        return False, product['unit_price'], None, snapshot.get('error')
    cost = product['unit_price']
    balance = Decimal(str(snapshot['balance']))
    if balance < cost:
        return False, cost, balance, 'موجودی سرویس تأمین برای این گیفت‌کارت کافی نیست.'
    return True, cost, balance, None


def purchase_gift_card(product_id, *, quantity=1, idempotency_key=None):
    """خرید کد گیفت از G2Bulk. خروجی شامل کدهای تحویل است."""
    if not is_configured():
        return {'ok': False, 'error': 'API key not configured'}
    try:
        product_id = int(product_id)
        quantity = int(quantity or 1)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'شناسه یا تعداد گیفت‌کارت نامعتبر است.'}
    if product_id <= 0 or quantity != 1:
        return {'ok': False, 'error': 'فقط خرید تکی گیفت‌کارت مجاز است.'}
    data = _request(
        'POST',
        f'/products/{product_id}/purchase',
        {'quantity': quantity},
        idempotency_key=idempotency_key,
    )
    return _normalize_gift_purchase(data)


def poll_gift_card_delivery(provider_order_id):
    if not is_configured() or not str(provider_order_id or '').strip():
        return {'ok': False, 'error': 'شناسه سفارش گیفت‌کارت موجود نیست.'}
    provider_id = str(provider_order_id).strip()
    data = _request('GET', f'/orders/{provider_id}/delivery')
    http_status = data.get('_http_status')
    if http_status == 410 or str(data.get('status') or '').upper() in {
        'REFUNDED', 'FAILED', 'CANCELED', 'CANCELLED',
    }:
        return {
            'ok': False,
            'status': 'FAILED',
            'refunded': True,
            'error': data.get('message') or 'سفارش گیفت‌کارت ناموفق بود و برگشت خورد.',
        }
    return _normalize_gift_purchase(data, fallback_order_id=provider_id)


def _normalize_gift_purchase(data, fallback_order_id=None):
    status = str(data.get('status') or '').strip().upper()
    codes = [
        str(item).strip()
        for item in (data.get('delivery_items') or [])
        if str(item).strip()
    ]
    provider_order_id = data.get('order_id') or fallback_order_id
    if status == 'COMPLETED' and codes:
        return {
            'ok': True,
            'status': 'COMPLETED',
            'order_id': provider_order_id,
            'codes': codes,
            'cost_usd': data.get('total_price') or data.get('unit_price'),
        }
    if status in {'PENDING', 'PROCESSING'} or (
        data.get('success') and data.get('poll_url') and not codes
    ):
        return {
            'ok': True,
            'status': 'PROCESSING' if status == 'PROCESSING' else 'PENDING',
            'order_id': provider_order_id,
            'codes': [],
            'poll_url': data.get('poll_url') or '',
        }
    if data.get('_transport_uncertain'):
        return {
            'ok': False,
            'uncertain': True,
            'order_id': provider_order_id,
            'error': data.get('message') or 'وضعیت خرید گیفت‌کارت نامشخص است.',
        }
    return {
        'ok': False,
        'status': status or 'FAILED',
        'order_id': provider_order_id,
        'error': data.get('message') or 'خرید گیفت‌کارت از G2Bulk ناموفق بود.',
    }


def gift_card_idempotency_key(order_pk, gift_pk):
    return str(uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f'atomicbot-gift-{order_pk}-{gift_pk}',
    ))


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
    # The live API values are parsed as Decimal. Mixing a float epsilon with
    # Decimal raises TypeError and used to break every product click.
    available = Decimal(str(snapshot['balance'])) >= Decimal(str(cost))
    return available, cost, snapshot['balance'], (
        None if available else 'موجودی سرویس تأمین برای این بسته کافی نیست.'
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
    callback_url='', game_code=None,
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

    code = str(game_code or _game_code()).strip() or _game_code()
    data = _request(
        'POST',
        f'/games/{code}/order',
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


def stars_idempotency_key(order_pk, star_pk):
    return str(uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f'atomicbot-stars-{order_pk}-{star_pk}',
    ))


def get_stars_snapshot(force=False):
    """کاتالوگ و موجودی دلاری استارز تلگرام — جدا از کش فری‌فایر."""
    if not is_configured():
        return {'ok': False, 'error': 'API key not configured'}
    now = time.monotonic()
    cached = _stars_cache.get('value')
    if cached and not cached.get('ok') and now - _stars_cache.get('at', 0) < 60:
        return cached
    if (
        not force and cached
        and now - _stars_cache.get('at', 0) < _STARS_CACHE_SECONDS
    ):
        return cached
    with _stars_refresh_lock:
        now = time.monotonic()
        cached = _stars_cache.get('value')
        age = now - _stars_cache.get('at', 0)
        if cached and (
            (not force and age < _STARS_CACHE_SECONDS)
            or (force and cached.get('ok') and age < _FORCED_REFRESH_COALESCE_SECONDS)
            or (not cached.get('ok') and age < 60)
        ):
            return cached
        me = _request('GET', '/getMe')
        if not me.get('success') or me.get('balance') is None:
            result = {
                'ok': False,
                'error': me.get('message') or 'دریافت موجودی G2Bulk ناموفق بود.',
            }
            _stars_cache.update(at=now, value=result)
            return result
        catalogue = _request('GET', f'/games/{TELEGRAM_GAME_CODE}/catalogue')
        if not catalogue.get('success'):
            result = {
                'ok': False,
                'error': catalogue.get('message') or 'دریافت کاتالوگ استارز ناموفق بود.',
            }
            _stars_cache.update(at=now, value=result)
            return result
        try:
            balance = Decimal(str(me['balance']))
        except (InvalidOperation, TypeError, ValueError):
            result = {'ok': False, 'error': 'موجودی برگشتی G2Bulk معتبر نیست.'}
            _stars_cache.update(at=now, value=result)
            return result
        items = []
        prices_by_name = {}
        for item in catalogue.get('catalogues') or []:
            name = str(item.get('name') or '').strip()
            stars = parse_stars_amount(name)
            if not name or not stars_amount_for_sale(stars):
                continue
            try:
                cost = Decimal(str(item.get('amount')))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if cost <= 0:
                continue
            prices_by_name[_normalise_catalogue_name(name)] = cost
            items.append({
                'name': name,
                'stars': stars,
                'cost_usd': cost,
            })
        items.sort(key=lambda row: (row['stars'], row['cost_usd']))
        result = {
            'ok': True,
            'balance': balance,
            'items': items,
            'prices_by_name': prices_by_name,
        }
        _stars_cache.update(at=now, value=result)
        return result


def can_fulfill_stars(catalogue_name, force=False):
    snapshot = get_stars_snapshot(force=force)
    if not snapshot.get('ok'):
        return False, None, None, snapshot.get('error')
    cost = snapshot.get('prices_by_name', {}).get(
        _normalise_catalogue_name(catalogue_name)
    )
    if cost is None:
        return False, None, snapshot.get('balance'), 'این مقدار استارز در کاتالوگ زنده نیست.'
    available = Decimal(str(snapshot['balance'])) >= Decimal(str(cost))
    return available, cost, snapshot['balance'], (
        None if available else 'موجودی سرویس تأمین برای این استارز کافی نیست.'
    )
