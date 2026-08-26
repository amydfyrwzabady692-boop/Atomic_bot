"""سرور HTTP سبک برای callback زرین‌پال روی VPS."""
import logging
import os
import asyncio
from aiohttp import web

logger = logging.getLogger(__name__)

_HTML_PAY_OK = (
    "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
    "<h2>پرداخت ثبت شد</h2><p>به ربات تلگرام برگرد؛ وضعیت سفارش برایت ارسال می‌شود.</p>"
    "</body></html>"
)
_HTML_PAY_FAIL = (
    "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
    "<h2>پرداخت ناموفق یا لغو شد</h2><p>به ربات برگرد و دوباره تلاش کن. VPN را خاموش کن.</p>"
    "</body></html>"
)
_HTML_WALLET_OK = (
    "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
    "<h2>شارژ ثبت شد</h2><p>به ربات تلگرام برگرد.</p>"
    "</body></html>"
)
_HTML_WALLET_FAIL = (
    "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
    "<h2>شارژ ناموفق</h2><p>VPN را خاموش کن و دوباره تلاش کن.</p>"
    "</body></html>"
)


async def extract_gateway_params(request):
    """Query + POST body. زرین‌پال گاهی query سفارشی مثل order را حذف می‌کند."""
    params = {str(key): str(value) for key, value in request.rel_url.query.items()}
    if request.method == 'POST':
        content_type = (request.content_type or '').lower()
        try:
            if 'json' in content_type:
                body = await request.json()
                if isinstance(body, dict):
                    for key, value in body.items():
                        if params.get(key) or value is None:
                            continue
                        params[str(key)] = str(value)
            else:
                posted = await request.post()
                for key in posted.keys():
                    if not params.get(key):
                        params[key] = str(posted.get(key) or '')
        except Exception:
            logger.exception('gateway callback body parse failed')
    order_id = (
        params.get('order') or params.get('order_id') or params.get('OrderId') or ''
    ).strip()
    authority = (
        params.get('Authority') or params.get('authority') or params.get('AuthorityId') or ''
    ).strip()
    status = (params.get('Status') or params.get('status') or '').strip()
    return order_id, authority, status


def _status_means_ok(status):
    """NOK یعنی انصراف. خالی را تأیید می‌کنیم تا verify بانک تصمیم بگیرد."""
    value = str(status or '').strip().upper()
    if value in ('NOK', 'NO', 'CANCEL', 'CANCELED', 'CANCELLED', 'FAILED'):
        return False
    return True


def create_web_app(bot_app):
    app = web.Application()

    async def health(_request):
        return web.Response(text='ok')

    async def ready(_request):
        try:
            def check_db():
                from db import get_conn
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute('SELECT 1')
                    return cur.fetchone()[0] == 1

            ok = await asyncio.wait_for(asyncio.to_thread(check_db), timeout=4)
            if ok:
                return web.json_response({'status': 'ready'})
        except Exception:
            logger.exception('readiness check failed')
        return web.json_response({'status': 'not_ready'}, status=503)

    async def payment_callback(request):
        order_id, authority, status = await extract_gateway_params(request)
        status_ok = _status_means_ok(status)
        logger.info(
            'Zarinpal order-callback method=%s order=%s authority=%s status=%s',
            request.method, order_id or '-', authority or '-', status or '-',
        )
        try:
            from db import get_order_by_authority
            from handlers.payment import (
                process_zarinpal_callback, process_zarinpal_wallet_callback,
            )
            if not order_id and authority:
                row = await asyncio.to_thread(get_order_by_authority, authority)
                if row:
                    order_id = str(row[0])
                    logger.info(
                        'Zarinpal callback resolved order=%s from authority',
                        order_id,
                    )
            if order_id:
                ok, detail = await process_zarinpal_callback(
                    bot_app.bot, int(order_id), authority, status_ok
                )
                logger.info(
                    'Zarinpal callback order=%s ok=%s detail=%s',
                    order_id, ok, detail,
                )
                return web.Response(
                    text=_HTML_PAY_OK if ok else _HTML_PAY_FAIL,
                    content_type='text/html',
                )
            if authority:
                ok, detail = await process_zarinpal_wallet_callback(
                    bot_app.bot, authority, status_ok
                )
                logger.info(
                    'Zarinpal callback fell back to wallet authority=%s ok=%s detail=%s',
                    authority, ok, detail,
                )
                return web.Response(
                    text=_HTML_PAY_OK if ok else _HTML_PAY_FAIL,
                    content_type='text/html',
                )
            logger.warning('Zarinpal callback missing order and authority')
            return web.Response(text=_HTML_PAY_FAIL, content_type='text/html')
        except Exception:
            logger.exception('payment callback failed')
            return web.Response(text=_HTML_PAY_FAIL, content_type='text/html')

    async def wallet_callback(request):
        _order_id, authority, status = await extract_gateway_params(request)
        status_ok = _status_means_ok(status)
        logger.info(
            'Zarinpal wallet-callback method=%s authority=%s status=%s',
            request.method, authority or '-', status or '-',
        )
        if not authority:
            return web.Response(text=_HTML_WALLET_FAIL, content_type='text/html')
        try:
            from handlers.payment import process_zarinpal_wallet_callback
            ok, detail = await process_zarinpal_wallet_callback(
                bot_app.bot, authority, status_ok
            )
            logger.info(
                'Zarinpal wallet-callback authority=%s ok=%s detail=%s',
                authority, ok, detail,
            )
            return web.Response(
                text=_HTML_WALLET_OK if ok else _HTML_WALLET_FAIL,
                content_type='text/html',
            )
        except Exception:
            logger.exception('wallet callback failed')
            return web.Response(text=_HTML_WALLET_FAIL, content_type='text/html')

    async def g2bulk_callback(request):
        order_id = request.rel_url.query.get('order')
        info_id = request.rel_url.query.get('item')
        token = request.rel_url.query.get('token')
        try:
            import g2bulk
            if not g2bulk.verify_callback_token(order_id, info_id, token):
                return web.json_response(
                    {'success': False, 'message': 'invalid token'}, status=403
                )
            payload = await request.json()
            provider_order_id = payload.get('order_id')
            status = str(payload.get('status') or '').strip().upper()
            player_id = payload.get('player_id')
            player_name = payload.get('player_name') or ''
            if status not in ('COMPLETED', 'FAILED', 'CANCELED'):
                return web.json_response({'success': True, 'ignored': True})
            from db import apply_g2bulk_webhook
            ok, result = await asyncio.to_thread(
                apply_g2bulk_webhook,
                int(order_id), int(info_id), provider_order_id,
                player_id, status, player_name,
            )
            if not ok:
                logger.warning(
                    'G2Bulk callback rejected order=%s item=%s reason=%s',
                    order_id, info_id, result,
                )
                return web.json_response(
                    {'success': False, 'message': result}, status=409
                )
            logger.info(
                'G2Bulk callback applied order=%s item=%s status=%s result=%s',
                order_id, info_id, status, result,
            )
            # ریفاند فوری → خبر به کاربر و ادمین
            if str(result).startswith('refunded:'):
                try:
                    amount = int(str(result).split(':', 1)[1])
                except (TypeError, ValueError):
                    amount = 0
                try:
                    from refund_notify import notify_g2_refund
                    await notify_g2_refund(
                        bot_app.bot, int(order_id), amount=amount,
                    )
                except Exception:
                    logger.exception(
                        'Could not notify refund from G2Bulk callback order=%s',
                        order_id,
                    )
            elif result == 'delivered':
                # نوتیف موفقیت را حلقه reconcile هم می‌فرستد؛ اینجا فقط لاگ کافی است
                pass
            return web.json_response({'success': True, 'result': result})
        except (TypeError, ValueError):
            return web.json_response(
                {'success': False, 'message': 'invalid payload'}, status=400
            )
        except Exception:
            logger.exception('G2Bulk callback failed')
            return web.json_response(
                {'success': False, 'message': 'callback failed'}, status=500
            )

    app.router.add_get('/health', health)
    app.router.add_get('/ready', ready)
    app.router.add_get('/payment/callback', payment_callback)
    app.router.add_get('/payment/wallet-callback', wallet_callback)
    # بعضی کلاینت‌ها POST هم می‌زنند
    app.router.add_post('/payment/callback', payment_callback)
    app.router.add_post('/payment/wallet-callback', wallet_callback)
    app.router.add_post('/g2bulk/callback', g2bulk_callback)
    return app


async def start_web_server(bot_app):
    port = int(os.getenv('WEB_PORT', '8080'))
    web_app = create_web_app(bot_app)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.getLogger(__name__).info('Payment callback server on :%s', port)
    return runner
