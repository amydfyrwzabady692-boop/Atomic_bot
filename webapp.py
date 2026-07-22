"""سرور HTTP سبک برای callback زرین‌پال روی VPS."""
import logging
import os
from aiohttp import web

logger = logging.getLogger(__name__)


def create_web_app(bot_app):
    app = web.Application()

    async def health(_request):
        return web.Response(text='ok')

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
        if status != 'OK' or not authority:
            return web.Response(text=html_fail, content_type='text/html')
        try:
            from db import get_conn, complete_wallet_charge_by_authority
            from payments import verify_payment
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    'SELECT t."Amount", t."IsPaid", w."UserId", u."TelegramId" '
                    'FROM "WalletTransactions" t '
                    'JOIN "Wallets" w ON w."Id"=t."WalletId" '
                    'LEFT JOIN "Users" u ON u."Id"=w."UserId" '
                    'WHERE t."Authority"=%s',
                    (authority,),
                )
                row = cur.fetchone()
            if not row:
                return web.Response(text=html_fail, content_type='text/html')
            amount, is_paid, user_id, telegram_id = row
            if not is_paid:
                ok, ref = verify_payment(amount, authority)
                if not ok:
                    return web.Response(text=html_fail, content_type='text/html')
                done, _uid, amt, new_bal = complete_wallet_charge_by_authority(authority)
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
                        pass
            return web.Response(text=html_ok, content_type='text/html')
        except Exception:
            logger.exception('wallet callback failed')
            return web.Response(text=html_fail, content_type='text/html')

    app.router.add_get('/health', health)
    app.router.add_get('/payment/callback', payment_callback)
    app.router.add_get('/payment/wallet-callback', wallet_callback)
    # بعضی کلاینت‌ها POST هم می‌زنند
    app.router.add_post('/payment/callback', payment_callback)
    app.router.add_post('/payment/wallet-callback', wallet_callback)
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
