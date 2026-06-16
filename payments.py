import json
import urllib.request
import urllib.error
import os
from dotenv import load_dotenv

load_dotenv()

_SANDBOX = os.getenv('ZARINPAL_SANDBOX', '1') == '1'
_MERCHANT = os.getenv('ZARINPAL_MERCHANT', '')


def _base():
    return 'https://sandbox.zarinpal.com/pg/v4/payment/' if _SANDBOX else 'https://payment.zarinpal.com/pg/v4/payment/'


def _start_base():
    return 'https://sandbox.zarinpal.com/pg/StartPay/' if _SANDBOX else 'https://payment.zarinpal.com/pg/StartPay/'


def _post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                  headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {}


def request_payment(amount_toman, description, callback_url, mobile=''):
    payload = {
        'merchant_id': _MERCHANT,
        'amount': int(amount_toman),
        'currency': 'IRT',
        'description': description,
        'callback_url': callback_url,
        'metadata': {'mobile': mobile or ''},
    }
    try:
        res = _post(_base() + 'request.json', payload)
    except Exception:
        return None, None
    data = res.get('data') or {}
    if data.get('code') == 100 and data.get('authority'):
        return data['authority'], _start_base() + data['authority']
    return None, None


def verify_payment(amount_toman, authority):
    payload = {
        'merchant_id': _MERCHANT,
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
