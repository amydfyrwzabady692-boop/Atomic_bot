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
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {}


def request_payment(amount_toman, description, callback_url, mobile=''):
    """خروجی: (authority, pay_url, error_message)"""
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
    if mobile_str:
        payload['metadata'] = {'mobile': mobile_str}
    try:
        res = _post(_base() + 'request.json', payload)
    except Exception as e:
        print(f'[ZARINPAL] request exception: {e}')
        return None, None, f'خطای ارتباط با زرین‌پال: {e}'

    if not res:
        return None, None, 'پاسخ خالی از زرین‌پال (احتمالاً قطعی شبکه سرور).'

    data = res.get('data') or {}
    errors = res.get('errors')
    if data.get('code') == 100 and data.get('authority'):
        return data['authority'], _start_base() + data['authority'], None

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


def verify_payment(amount_toman, authority):
    merchant = _merchant()
    authority = str(authority or '').strip()
    if not merchant or not authority or len(authority) > 100:
        return False, None
    try:
        amount_toman = checked_amount(
            amount_toman, minimum=MIN_GATEWAY_AMOUNT, label='مبلغ تأیید درگاه'
        )
    except ValueError:
        return False, None
    payload = {
        'merchant_id': merchant,
        'amount': amount_toman,
        'authority': authority,
    }
    try:
        res = _post(_base() + 'verify.json', payload)
    except Exception:
        return False, None
    data = res.get('data') or {}
    if data.get('code') in (100, 101) and data.get('ref_id'):
        return True, str(data['ref_id'])
    return False, None
