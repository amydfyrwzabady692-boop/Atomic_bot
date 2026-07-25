"""ثبت محافظه‌کارانه نرخ دلار و محاسبه سود ناخالص فروش جم."""
import json
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

NOBITEX_USDT_IRT_ORDERBOOK = (
    'https://apiv2.nobitex.ir/v3/orderbook/USDTIRT'
)
_rate_cache = {'at': 0.0, 'value': None}


def calculate_gross_profit(sale_toman, supplier_cost_usd, usd_toman_rate):
    """خروجی (هزینه تامین به تومان، سود ناخالص) با گردکردن حسابداری."""
    try:
        sale = Decimal(str(sale_toman))
        cost_usd = Decimal(str(supplier_cost_usd))
        rate = Decimal(str(usd_toman_rate))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('ورودی محاسبه سود نامعتبر است.') from exc
    if sale <= 0 or cost_usd <= 0 or rate <= 0:
        raise ValueError('فروش، هزینه و نرخ دلار باید مثبت باشند.')
    supplier_toman = int(
        (cost_usd * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    )
    return supplier_toman, int(sale) - supplier_toman


def allocate_sale_amount(net_order_toman, item_subtotal_toman, order_items_toman):
    """سهم فروش هر قلم را پس از تخفیف به‌صورت متناسب و گرد‌شده برمی‌گرداند."""
    try:
        net = Decimal(str(net_order_toman))
        item = Decimal(str(item_subtotal_toman))
        total = Decimal(str(order_items_toman))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('مبالغ تخصیص فروش نامعتبر است.') from exc
    if net <= 0 or item <= 0 or total <= 0 or item > total:
        raise ValueError('مبالغ تخصیص فروش خارج از محدوده است.')
    return int(
        (net * item / total).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    )


def calculate_live_pack_costs(packages, supplier_prices_usd, usd_toman_rate):
    """Return current supplier cost/profit for configured gem packages."""
    rows = []
    prices = supplier_prices_usd or {}
    for package_id, title, amount, sale_toman in packages:
        try:
            amount = int(amount)
            sale_toman = int(sale_toman)
            cost_usd = prices.get(amount)
            if cost_usd is None:
                continue
            cost_toman, gross_profit = calculate_gross_profit(
                sale_toman, cost_usd, usd_toman_rate
            )
        except (TypeError, ValueError, InvalidOperation):
            continue
        margin = gross_profit * 100 / sale_toman if sale_toman else 0
        rows.append({
            'package_id': int(package_id),
            'title': str(title or ''),
            'amount': amount,
            'sale_toman': sale_toman,
            'cost_usd': float(cost_usd),
            'cost_toman': cost_toman,
            'gross_profit_toman': gross_profit,
            'margin_percent': margin,
        })
    return rows


def _manual_rate():
    raw = (os.getenv('USD_TOMAN_RATE') or '').strip()
    if not raw:
        try:
            from db import get_setting
            raw = str(get_setting('usd_toman_rate', '') or '').strip()
        except Exception:
            raw = ''
    try:
        value = int(raw.replace(',', ''))
    except (TypeError, ValueError):
        return None
    return value if 10_000 <= value <= 10_000_000 else None


def _request_json(url):
    req = urllib.request.Request(
        url,
        headers={'Accept': 'application/json', 'User-Agent': 'AtomicBot/1.0'},
        method='GET',
    )
    with urllib.request.urlopen(req, timeout=6) as response:
        return json.loads(response.read().decode('utf-8'))


def get_usd_toman_rate(force=False):
    """بهترین ask بازار USDTIRT را از ریال به تومان تبدیل و cache می‌کند."""
    now = time.monotonic()
    cached = _rate_cache.get('value')
    if not force and cached and now - _rate_cache.get('at', 0) < 60:
        return cached
    try:
        data = _request_json(NOBITEX_USDT_IRT_ORDERBOOK)
        asks = data.get('asks') or []
        if data.get('status') != 'ok' or not asks or not asks[0]:
            raise ValueError('orderbook معتبر نیست')
        # خروجی نوبیتکس برای USDTIRT به ریال است؛ هزینه جایگزینی را با
        # بهترین سفارش فروش (ask) و تقسیم بر ۱۰ به تومان ثبت می‌کنیم.
        raw_rial = Decimal(str(asks[0][0]))
        rate = int(
            (raw_rial / Decimal('10')).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP
            )
        )
        if not 10_000 <= rate <= 10_000_000:
            raise ValueError('نرخ خارج از محدوده ایمن است')
        observed_ms = int(data.get('lastUpdate') or 0) or None
        result = {
            'ok': True,
            'rate': rate,
            'source': 'nobitex_usdtirt_best_ask',
            'observed_ms': observed_ms,
            'fallback': False,
        }
    except (
        urllib.error.URLError, TimeoutError, OSError, ValueError,
        KeyError, TypeError, InvalidOperation, json.JSONDecodeError,
    ) as exc:
        manual = _manual_rate()
        if manual:
            result = {
                'ok': True,
                'rate': manual,
                'source': 'manual_fallback',
                'observed_ms': None,
                'fallback': True,
                'warning': str(exc),
            }
        else:
            result = {
                'ok': False,
                'error': 'نرخ زنده دلار دریافت نشد و نرخ دستی تنظیم نشده است.',
            }
    _rate_cache.update(at=now, value=result)
    return result


def get_purchase_rate_snapshot():
    """Fetch a fresh rate for an immutable purchase-profit snapshot."""
    return get_usd_toman_rate(force=True)
