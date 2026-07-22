"""درگاه زرین‌پال — درخواست و تایید پرداخت (تومان / IRT)."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / '.env')


def _sandbox():
    return os.getenv('ZARINPAL_SANDBOX', '0') == '1'


def _merchant():
    return (
        os.getenv('ZARINPAL_MERCHANT_ID')
        or os.getenv('ZARINPAL_MERCHANT')
        or ''
    ).strip()


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
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {}


def request_payment(amount_toman, description, callback_url, mobile=''):
    """خروجی: (authority, pay_url, error_message)"""
    merchant = _merchant()
    if not merchant:
        return None, None, 'مرچنت زرین‌پال تنظیم نشده است.'
    if not (callback_url or '').startswith('https://'):
        return None, None, 'آدرس بازگشت درگاه باید HTTPS باشد. SSL دامنه را فعال کن.'

    payload = {
        'merchant_id': merchant,
        'amount': int(amount_toman),
        'currency': 'IRT',
        'description': description,
        'callback_url': callback_url,
        'metadata': {'mobile': mobile or ''},
    }
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
    if not merchant or not authority:
        return False, None
    payload = {
        'merchant_id': merchant,
        'amount': int(amount_toman),
        'authority': authority,
    }
    try:
        res = _post(_base() + 'verify.json', payload)
    except Exception:
        return False, None
    data = res.get('data') or {}
    if data.get('code') in (100, 101):
        return True, data.get('ref_id')
    return False, None
