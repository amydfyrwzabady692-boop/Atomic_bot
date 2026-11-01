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
# اگر نوبیتکس از این سرور جواب نداد، تا ده دقیقه مستقیم سراغش نمی‌رویم و نرخ
# زنده را از سایت می‌گیریم؛ وگرنه هر خرید شش ثانیه پشت تایم‌اوتش می‌ماند.
_direct_failed = {'at': None}
_DIRECT_RETRY_SECONDS = 600


def usd_rate_markup():
    """تومانی که روی هر نرخ گرفته‌شده اضافه می‌شود تا مبنای قیمت‌ها شود.

    پیش‌فرض ۵٬۰۰۰ تومان (USD_RATE_MARKUP_TOMAN)؛ همان عدد سایت
    (account_shop/settings.py). روی نرخ زندهٔ نوبیتکس و روی نرخ دستیِ پشتیبان،
    هر دو، تا مبنا هیچ‌وقت با منبع نرخ عوض نشود.
    """
    raw = (os.getenv('USD_RATE_MARKUP_TOMAN') or '').strip()
    try:
        return max(0, int(raw)) if raw else 5000
    except ValueError:
        return 5000


SOURCE_LABELS = {
    'nobitex_usdtirt_best_ask': 'نوبیتکس، زنده',
    'site_nobitex': 'نوبیتکس از طریق سایت، زنده',
    'last_live': 'آخرین نرخ زنده — نرخ زنده الان نرسید',
    'manual_fallback': 'نرخ دستی پشتیبان — نرخ زنده الان نرسید',
}


def source_label(fx):
    return SOURCE_LABELS.get((fx or {}).get('source'), str((fx or {}).get('source') or '—'))


def market_rate(fx):
    """نرخ خودِ بازار از خروجی get_usd_toman_rate — برای بها و سود، نه قیمت فروش."""
    if not fx or not fx.get('ok'):
        return 0
    return int(fx.get('market_rate') or fx.get('rate') or 0)


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
            'cost_usd': Decimal(str(cost_usd)),
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
    """نرخ دلار برای قیمت‌گذاری: همیشه نرخ زندهٔ تازه + usd_rate_markup() (۵٬۰۰۰ تومان).

    `rate` مبنای قیمت‌گذاری است و هر جا قیمت فروش حساب می‌شود (جم، گیفت کارت،
    استارز، پرمیوم) همین را می‌خواند. `market_rate` خودِ نرخ بازار است، برای
    بهای واقعی و سود. افزایش فقط همین‌جا و فقط یک بار روی نرخ بازار می‌نشیند.

    نرخ زنده به این ترتیب گرفته می‌شود:
    ۱. مستقیم از نوبیتکس (بهترین ask بازار USDTIRT)؛
    ۲. اگر نشد، از سایت: سرور ربات خارج از ایران است و نوبیتکس معمولاً راهش
       نمی‌دهد، ولی سرور سایت داخل ایران است و همان نرخ زنده را دارد؛
    ۳. اگر هیچ‌کدام نشد، بالاترینِ «آخرین نرخ زندهٔ موفق» و «نرخ دستی پشتیبان»
       — هیچ‌وقت با نرخی پایین‌تر از آخرین نرخ زنده قیمت‌گذاری نمی‌شود.
    """
    now = time.monotonic()
    cached = _rate_cache.get('value')
    if not force and cached and now - _rate_cache.get('at', 0) < 60:
        return cached
    try:
        failed_at = _direct_failed.get('at')
        if failed_at is not None and now - failed_at < _DIRECT_RETRY_SECONDS:
            raise OSError('نوبیتکس چند دقیقه پیش از این سرور جواب نداد')
        try:
            data = _request_json(NOBITEX_USDT_IRT_ORDERBOOK)
        except Exception:
            _direct_failed['at'] = now
            raise
        _direct_failed['at'] = None
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
        result = _live_result(rate, 'nobitex_usdtirt_best_ask', observed_ms)
    except (
        urllib.error.URLError, TimeoutError, OSError, ValueError,
        KeyError, TypeError, InvalidOperation, json.JSONDecodeError,
    ) as exc:
        site_rate = _rate_from_site()
        if site_rate:
            result = _live_result(site_rate, 'site_nobitex', None)
        else:
            result = _fallback_result(str(exc))
    _rate_cache.update(at=now, value=result)
    return result


def _live_result(market, source, observed_ms):
    markup = usd_rate_markup()
    _remember_live(market)
    return {
        'ok': True,
        'rate': int(market) + markup,
        'market_rate': int(market),
        'markup': markup,
        'source': source,
        'observed_ms': observed_ms,
        'fallback': False,
    }


def _rate_from_site():
    """نرخ زندهٔ بازار از سایت (نوبیتکسِ سرور ایران)، یا None.

    فقط نرخ زنده پذیرفته می‌شود، نه نرخ پشتیبانِ سایت؛ و سایت نرخ *بازار*
    می‌دهد، بی‌افزایش — افزایش همین‌جا یک بار رویش می‌نشیند.
    """
    try:
        import site_api
        data = site_api.fetch_site_usd_rate()
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get('ok') or data.get('fallback'):
        return None
    if data.get('source') != 'nobitex_usdtirt_best_ask':
        return None
    try:
        market = int(data.get('market_rate'))
    except (TypeError, ValueError):
        return None
    return market if 10_000 <= market <= 10_000_000 else None


LIVE_RATE_KEY = 'usd_live_last'


def _remember_live(market):
    """آخرین نرخ زندهٔ موفق: «نرخ|زمان»؛ مبنای پشتیبان وقتی هیچ نرخ زنده‌ای نرسید."""
    from datetime import datetime, timezone
    try:
        from db import set_setting
        stamp = datetime.now(timezone.utc)
        last = _last_live()
        if last and last[0] == int(market) and (stamp - last[1]).total_seconds() < 600:
            return
        set_setting(LIVE_RATE_KEY, f'{int(market)}|{stamp.isoformat()}')
    except Exception:
        pass


def _last_live():
    """(نرخ بازار، زمان) آخرین نرخ زندهٔ موفق، یا None."""
    from datetime import datetime, timezone
    try:
        from db import get_setting
        raw = str(get_setting(LIVE_RATE_KEY, '') or '')
    except Exception:
        return None
    value, _, stamp = raw.partition('|')
    try:
        market = int(value)
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (market, when) if 10_000 <= market <= 10_000_000 else None


def _fallback_result(error):
    """هیچ نرخ زنده‌ای نرسید: بالاترینِ «آخرین نرخ زنده» و «نرخ دستی» + همان افزایش.

    بالاترین، چون اشتباهِ رو به پایین ضرر است: نرخ دستیِ کهنه و پایین نباید
    قیمت را از آخرین نرخ زنده پایین‌تر بیاورد؛ اگر مدیر عمداً نرخ دستیِ بالاتری
    گذاشته، همان اعمال می‌شود. هیچ‌کدام نبود: ok=False و قیمت‌ها دست نمی‌خورند.
    """
    candidates = []
    last = _last_live()
    if last:
        candidates.append((last[0], 'last_live'))
    manual = _manual_rate()
    if manual:
        candidates.append((manual, 'manual_fallback'))
    if not candidates:
        return {
            'ok': False,
            'error': 'نرخ زنده دلار دریافت نشد و نرخ دستی تنظیم نشده است.',
        }
    market, source = max(candidates, key=lambda item: item[0])
    markup = usd_rate_markup()
    return {
        'ok': True,
        'rate': market + markup,
        'market_rate': market,
        'markup': markup,
        'source': source,
        'observed_ms': None,
        'fallback': True,
        'warning': error,
    }


def get_purchase_rate_snapshot():
    """Fetch a fresh rate for an immutable purchase-profit snapshot.

    سود هر فروش با نرخ خودِ بازار ثبت می‌شود، نه با مبنای قیمت‌گذاری؛ پس
    ۵٬۰۰۰ تومانِ روی نرخ در گزارش سود، سود دیده می‌شود نه هزینه.
    """
    fx = get_usd_toman_rate(force=True)
    if fx.get('ok') and fx.get('market_rate'):
        return {**fx, 'rate': int(fx['market_rate'])}
    return fx
