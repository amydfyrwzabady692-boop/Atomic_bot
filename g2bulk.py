"""کلاینت G2Bulk — تایید آیدی و تحویل خودکار جم فری‌فایر (Middle East)."""
import json
import os
import uuid
import urllib.error
import urllib.request

BASE_URL = 'https://api.g2bulk.com/v1'
G2BULK_ME_AMOUNTS = (110, 231, 583, 1188, 2420)


def _api_key():
    return (os.getenv('G2BULK_API_KEY') or '').strip()


def _game_code():
    return (os.getenv('G2BULK_GAME_CODE') or 'freefire_me').strip()


def is_configured():
    return bool(_api_key())


def is_supported_amount(amount):
    return int(amount) in G2BULK_ME_AMOUNTS


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
            return json.loads(raw)
        except ValueError:
            return {'success': False, 'message': raw or str(e)}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {'success': False, 'message': str(e)}


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


def place_game_order(catalogue_name, player_id, remark='', idempotency_key=None):
    """ثبت سفارش شارژ. خروجی: {ok, order_id, status, player_name, error}"""
    if not is_configured():
        return {'ok': False, 'error': 'API key not configured'}

    body = {
        'catalogue_name': str(catalogue_name),
        'player_id': str(player_id).strip(),
    }
    if remark:
        body['remark'] = remark

    data = _request(
        'POST',
        f'/games/{_game_code()}/order',
        body,
        idempotency_key=idempotency_key,
    )
    if data.get('success') and data.get('order'):
        order = data['order']
        return {
            'ok': True,
            'order_id': order.get('order_id'),
            'status': order.get('status', 'PENDING'),
            'player_name': order.get('player_name', ''),
        }
    return {
        'ok': False,
        'error': data.get('message') or 'ثبت سفارش در G2Bulk ناموفق بود.',
    }


def idempotency_key(order_pk, gem_info_pk):
    return str(uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f'atomicbot-order-{order_pk}-gem-{gem_info_pk}',
    ))
