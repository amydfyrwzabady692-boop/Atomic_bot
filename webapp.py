"""سرور HTTP سبک برای callback زرین‌پال روی VPS."""
import logging
import os
import asyncio
from aiohttp import web

logger = logging.getLogger(__name__)


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
        order_id = request.rel_url.query.get('order')
        authority = request.rel_url.query.get('Authority') or request.rel_url.query.get('authority')
        status = request.rel_url.query.get('Status') or request.rel_url.query.get('status') or ''
        status_ok = status.upper() == 'OK'
        html_ok = (
            "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
            "<h2>پرداخت ثبت شد</h2><p>به ربات تلگرام برگرد؛ وضعیت سفارش برایت ارسال می‌شود.</p>"
            "</body></html>"
        )
        html_fail = (
            "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
            "<h2>پرداخت ناموفق یا لغو شد</h2><p>به ربات برگرد و دوباره تلاش کن. VPN را خاموش کن.</p>"
            "</body></html>"
        )
        if not order_id:
            return web.Response(text=html_fail, content_type='text/html')
        try:
            from handlers.payment import process_zarinpal_callback
            ok, detail = await process_zarinpal_callback(
                bot_app.bot, int(order_id), authority, status_ok
            )
            logger.info('Zarinpal callback order=%s ok=%s detail=%s', order_id, ok, detail)
            return web.Response(text=html_ok if ok else html_fail, content_type='text/html')
        except Exception:
            logger.exception('payment callback failed')
            return web.Response(text=html_fail, content_type='text/html')

    async def wallet_callback(request):
        authority = request.rel_url.query.get('Authority') or request.rel_url.query.get('authority')
        status = (request.rel_url.query.get('Status') or '').upper()
        html_ok = (
            "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
            "<h2>شارژ ثبت شد</h2><p>به ربات تلگرام برگرد.</p>"
            "</body></html>"
        )
        html_fail = (
            "<html><body style='font-family:tahoma;text-align:center;padding:40px'>"
            "<h2>شارژ ناموفق</h2><p>VPN را خاموش کن و دوباره تلاش کن.</p>"
            "</body></html>"
        )
        if not authority:
            return web.Response(text=html_fail, content_type='text/html')
        try:
            from db import (
                get_conn, complete_wallet_charge_by_authority, log_payment_attempt,
            )
            from payments import verify_payment
            def load_wallet_transaction():
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        'SELECT t."Id",t."Amount",t."IsPaid",w."UserId",u."TelegramId" '
                        'FROM "WalletTransactions" t '
                        'JOIN "Wallets" w ON w."Id"=t."WalletId" '
                        'LEFT JOIN "Users" u ON u."Id"=w."UserId" '
                        'WHERE t."Authority"=%s AND t."Kind"=\'charge\' '
                        'AND t."Authority" NOT LIKE \'wcard_%%\'',
                        (authority,),
                    )
                    return cur.fetchone()

            row = await asyncio.to_thread(load_wallet_transaction)
            if not row:
                return web.Response(text=html_fail, content_type='text/html')
            tx_id, amount, is_paid, user_id, telegram_id = row
            if status != 'OK':
                await asyncio.to_thread(
                    log_payment_attempt,
                    provider='zarinpal', event='wallet_callback',
                    status='canceled', amount=amount, wallet_tx_id=tx_id,
                    telegram_id=telegram_id, authority=authority,
                    message='user canceled',
                )
                return web.Response(text=html_fail, content_type='text/html')
            if not is_paid:
                ok, ref = await asyncio.to_thread(
                    verify_payment, amount, authority
                )
                if not ok:
                    await asyncio.to_thread(
                        log_payment_attempt,
                        provider='zarinpal', event='wallet_callback',
                        status='failed', amount=amount, wallet_tx_id=tx_id,
                        telegram_id=telegram_id, authority=authority,
                        message='verify failed',
                    )
                    return web.Response(text=html_fail, content_type='text/html')
                done, _uid, amt, new_bal = await asyncio.to_thread(
                    complete_wallet_charge_by_authority,
                    authority, verified_amount=amount, ref_id=ref,
                )
                if not done:
                    logger.warning(
                        'wallet callback verified but completion failed authority=%s',
                        authority,
                    )
                    return web.Response(text=html_fail, content_type='text/html')
                await asyncio.to_thread(
                    log_payment_attempt,
                    provider='zarinpal', event='wallet_verified',
                    status='success', amount=amt, wallet_tx_id=tx_id,
                    telegram_id=telegram_id, authority=authority, ref_id=ref,
                )
                if done and telegram_id:
                    try:
                        await bot_app.bot.send_message(
                            chat_id=int(telegram_id),
                            text=(
                                f"✅ کیف پول شارژ شد!\n"
                                f"مبلغ: {amt:,} تومان\n"
                                f"موجودی: {new_bal:,} تومان\n"
                                f"پیگیری: {ref}"
                            ),
                        )
                    except Exception:
                        logger.exception(
                            'wallet charged but Telegram notification failed tx=%s',
                            tx_id,
                        )
            return web.Response(text=html_ok, content_type='text/html')
        except Exception:
            logger.exception('wallet callback failed')
            return web.Response(text=html_fail, content_type='text/html')

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
