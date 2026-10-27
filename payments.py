"""درگاه زرین‌پال — درخواست و تایید پرداخت (تومان / IRT)."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from payment_safety import MIN_GATEWAY_AMOUNT, checked_amount

load_dotenv(dotenv_path=Path(__file__).parent / '.env')


def _sandbox():
    try:
        from db import get_setting
        value = get_setting('zarinpal_sandbox', os.getenv('ZARINPAL_SANDBOX', '0'))
    except Exception:
        value = os.getenv('ZARINPAL_SANDBOX', '0')
    return str(value) == '1'


def _merchant():
    env_value = (
        os.getenv('ZARINPAL_MERCHANT_ID')
        or os.getenv('ZARINPAL_MERCHANT')
        or ''
    ).strip()
    try:
        from db import get_setting
        return get_setting('zarinpal_merchant_id', env_value).strip()
    except Exception:
        return env_value


def _base():
    if _sandbox():
        return 'https://sandbox.zarinpal.com/pg/v4/payment/'
    return 'https://payment.zarinpal.com/pg/v4/payment/'


def _start_base():
    if _sandbox():
        return 'https://sandbox.zarinpal.com/pg/StartPay/'
    return 'https://payment.zarinpal.com/pg/StartPay/'


def _post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            result = json.loads(r.read().decode())
            return result if isinstance(result, dict) else {}
    except urllib.error.HTTPError as e:
        try:
            result = json.loads(e.read().decode())
            if isinstance(result, dict):
                result['_http_status'] = e.code
                if e.code >= 500:
                    result['_transport_error'] = True
                return result
            return {'_http_status': e.code}
        except Exception:
            return {
                '_http_status': e.code,
                '_transport_error': e.code >= 500,
            }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {'_transport_error': True}


def make_pay_url(authority):
    base = (
        os.getenv('PAYMENT_CALLBACK_BASE')
        or os.getenv('BOT_DOMAIN', 'https://botatomic.atomicshop.ir')
    ).strip().rstrip('/')
    if not base.startswith('http'):
        base = f"https://{base}"
    return f"{base}/pay/{authority}"


def request_payment(amount_toman, description, callback_url, mobile='', card_pan=''):
    """خروجی: (authority, pay_url, error_message)

    card_pan: شمارهٔ ۱۶ رقمیِ کارتِ احرازشده؛ زرین‌پال فقط از همین کارت پرداخت
    می‌پذیرد.
    """
    merchant = _merchant()
    if not merchant:
        return None, None, 'مرچنت زرین‌پال تنظیم نشده است.'
    try:
        amount_toman = checked_amount(
            amount_toman, minimum=MIN_GATEWAY_AMOUNT, label='مبلغ درگاه'
        )
    except ValueError as e:
        return None, None, str(e)
    parsed_callback = urllib.parse.urlparse(str(callback_url or ''))
    if (
        parsed_callback.scheme != 'https'
        or not parsed_callback.hostname
        or parsed_callback.username
        or parsed_callback.password
    ):
        return None, None, 'آدرس بازگشت درگاه باید HTTPS باشد. SSL دامنه را فعال کن.'

    payload = {
        'merchant_id': merchant,
        'amount': amount_toman,
        'currency': 'IRT',
        'description': (description or 'Atomic Bot')[:255],
        'callback_url': callback_url,
    }
    # زرین‌پال با mobile خالی/null خطا می‌دهد — فقط اگر شماره واقعی بود بفرست
    mobile_str = str(mobile).strip() if mobile not in (None, '') else ''
    metadata = {}
    if mobile_str:
        metadata['mobile'] = mobile_str
    pan = ''.join(ch for ch in str(card_pan or '') if ch.isdigit())
    if len(pan) == 16:
        metadata['card_pan'] = pan
    if metadata:
        payload['metadata'] = metadata
    try:
        res = _post(_base() + 'request.json', payload)
    except Exception as e:
        print(f'[ZARINPAL] request exception: {e}')
        return None, None, f'خطای ارتباط با زرین‌پال: {e}'

    if not res or res.get('_transport_error'):
        return None, None, 'پاسخ خالی از زرین‌پال (احتمالاً قطعی شبکه سرور).'

    data = res.get('data') or {}
    errors = res.get('errors')
    if data.get('code') == 100 and data.get('authority'):
        return data['authority'], make_pay_url(data['authority']), None

    # پیام خطای خوانا از زرین‌پال
    msg = None
    if isinstance(errors, dict):
        msg = errors.get('message') or str(errors)
    elif isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            msg = first.get('message') or str(first)
        else:
            msg = str(first)
    if not msg:
        msg = f"کد خطا: {data.get('code') or res}"
    print(f'[ZARINPAL] request failed: data={data} errors={errors}')
    return None, None, msg


def verify_payment_detailed(amount_toman, authority):
    """خروجی status/ref: verified | not_paid | unavailable | invalid."""
    merchant = _merchant()
    authority = str(authority or '').strip()
    if not merchant or not authority or len(authority) > 100:
        return 'invalid', None
    try:
        amount_toman = checked_amount(
            amount_toman, minimum=MIN_GATEWAY_AMOUNT, label='مبلغ تأیید درگاه'
        )
    except ValueError:
        return 'invalid', None
    payload = {
        'merchant_id': merchant,
        'amount': amount_toman,
        'authority': authority,
    }
    try:
        res = _post(_base() + 'verify.json', payload)
    except Exception:
        return 'unavailable', None
    if not res or res.get('_transport_error'):
        return 'unavailable', None
    data = res.get('data') or {}
    if data.get('code') in (100, 101) and data.get('ref_id'):
        if not _paid_from_bound_card(authority, data, amount_toman):
            return 'not_paid', None
        return 'verified', str(data['ref_id'])
    return 'not_paid', None


def card_pan_matches(card_pan, card_number):
    """شش رقم اول و چهار رقم آخرِ شمارهٔ ماسک‌شدهٔ زرین‌پال با کارتِ احرازشده."""
    digits = ''.join(ch for ch in str(card_pan or '') if ch.isdigit())
    number = ''.join(ch for ch in str(card_number or '') if ch.isdigit())
    return len(digits) >= 10 and len(number) == 16 and digits[:6] == number[:6] and digits[-4:] == number[-4:]


def _paid_from_bound_card(authority, data, amount_toman):
    """پرداخت از همان کارتی آمده که لینک به آن گره خورده بود؟

    لینک‌هایی که پیش از این تغییر ساخته شده‌اند گره ندارند و مثل قبل پذیرفته
    می‌شوند. پرداخت از کارتِ دیگر: تراکنش برگردانده و «پرداخت‌نشده» حساب
    می‌شود — پس هیچ فراخوانی (callback، بررسی دستی، بررسی پس‌زمینه) سفارش را
    تحویل یا کیف پول را شارژ نمی‌کند. خبر به ادمین و خریدار را حلقهٔ ربات می‌دهد.
    """
    try:
        from db import get_gateway_card_binding, mark_gateway_mismatch
        binding = get_gateway_card_binding(authority)
    except Exception as exc:
        print(f'[ZARINPAL] card binding lookup failed: {exc}')
        return True
    if not binding:
        return True
    if binding[3] == 'mismatch':
        return False
    paid_pan = str(data.get('card_pan') or '')
    if len(''.join(ch for ch in paid_pan if ch.isdigit())) < 10:
        # زرین‌پال شمارهٔ خوانا نداد؛ لینک به همان کارت محدود بود.
        return True
    if card_pan_matches(paid_pan, binding[2]):
        return True
    reversed_ok = reverse_payment(authority)
    print(f'[ZARINPAL] paid from a card that is not the verified one; reversed={reversed_ok}')
    mark_gateway_mismatch(authority, paid_pan, data.get('ref_id'), amount_toman, reversed_ok)
    return False


def reverse_payment(authority):
    """برگرداندنِ تراکنش به کارتِ پرداخت‌کننده (تا ۳۰ دقیقه بعد از پرداخت)."""
    merchant = _merchant()
    if not merchant or not authority:
        return False
    try:
        res = _post(_base() + 'reverse.json', {'merchant_id': merchant, 'authority': authority})
    except Exception:
        return False
    return (res.get('data') or {}).get('code') == 100


def verify_payment(amount_toman, authority):
    status, ref_id = verify_payment_detailed(amount_toman, authority)
    return status == 'verified', ref_id
