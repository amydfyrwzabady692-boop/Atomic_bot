import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

from handlers.start import start_handler, help_handler, home_callback, myid_handler
from handlers.store import store_menu
from handlers.gems import gems_menu, show_gem, gem_conversation_handler
from handlers.sensitivity import sens_menu
from handlers.cart import show_cart
from handlers.payment import (
    payment_conversation_handler, start_zarinpal, check_zarinpal,
    start_card, pay_wallet, cancel_order, admin_approve, admin_reject,
)
from handlers.wallet import (
    wallet_menu, wallet_charge_preset, wallet_check, wallet_conversation_handler,
)
from handlers.account import my_account, my_orders
from webapp import start_web_server

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)

MENU_TEXTS = {
    '💎 جم فری‌فایر': gems_menu,
    '💰 کیف پول': wallet_menu,
    '📦 سفارش‌های من': my_orders,
    '👤 حساب من': my_account,
    '🛍 فروشگاه اکانت': store_menu,
    '🎯 پک سنس': sens_menu,
    '🛒 سبد خرید': show_cart,
}


async def support_stub(update, ctx):
    from handlers.support import support_menu
    await support_menu(update, ctx)


MENU_TEXTS['🎧 پشتیبانی'] = support_stub


async def text_router(update, ctx):
    handler = MENU_TEXTS.get(update.message.text)
    if handler:
        await handler(update, ctx)
    else:
        await update.message.reply_text("❓ متوجه نشدم. از منوی پایین انتخاب کن 👇")


async def post_init(app):
    await start_web_server(app)
    _log_startup_checks()


def _log_startup_checks():
    log = logging.getLogger(__name__)
    ok = True
    if not os.getenv('BOT_TOKEN'):
        log.error('BOT_TOKEN missing')
        ok = False
    merchant = os.getenv('ZARINPAL_MERCHANT_ID') or os.getenv('ZARINPAL_MERCHANT')
    if not merchant:
        log.error('ZARINPAL_MERCHANT_ID missing — درگاه کار نمی‌کند')
        ok = False
    else:
        log.info('Zarinpal merchant configured')
    if not os.getenv('G2BULK_API_KEY'):
        log.error('G2BULK_API_KEY missing — تایید آیدی/تحویل کار نمی‌کند')
        ok = False
    else:
        log.info('G2Bulk configured')
    cb = os.getenv('PAYMENT_CALLBACK_BASE') or ''
    log.info('Payment callback base: %s', cb or '(empty)')
    if not os.getenv('ADMIN_CHAT_ID'):
        log.warning('ADMIN_CHAT_ID empty — رسید کارت‌به‌کارت به ادمین نمی‌رسد. /myid بزن')
    try:
        from db import get_conn, get_gems_by_id
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute('SELECT 1')
        gems = get_gems_by_id()
        log.info('DB OK — %s gem packages loaded', len(gems))
    except Exception as e:
        log.error('DB connection FAILED: %s — روی وی‌پی‌اس DB_HOST را درست بگذار', e)
        ok = False
    if ok:
        log.info('Startup checks passed')
    else:
        log.warning('Startup checks found problems — see errors above')


def main():
    token = os.getenv('BOT_TOKEN')
    if not token or token in ('YOUR_TOKEN_HERE', 'YOUR_TELEGRAM_BOT_TOKEN'):
        raise RuntimeError("توکن ربات را در .env تنظیم کن: BOT_TOKEN=...")

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler('start', start_handler))
    app.add_handler(CommandHandler('help', help_handler))
    app.add_handler(CommandHandler('myid', myid_handler))

    app.add_handler(gem_conversation_handler())
    app.add_handler(payment_conversation_handler())
    app.add_handler(wallet_conversation_handler())

    app.add_handler(CallbackQueryHandler(home_callback, pattern='^home$'))
    app.add_handler(CallbackQueryHandler(gems_menu, pattern='^gems$'))
    app.add_handler(CallbackQueryHandler(show_gem, pattern=r'^gem_\d+$'))
    app.add_handler(CallbackQueryHandler(show_gem, pattern='^noop$'))

    app.add_handler(CallbackQueryHandler(start_zarinpal, pattern=r'^pay_zp_\d+$'))
    app.add_handler(CallbackQueryHandler(check_zarinpal, pattern=r'^zp_check_\d+$'))
    app.add_handler(CallbackQueryHandler(start_card, pattern=r'^pay_card_\d+$'))
    app.add_handler(CallbackQueryHandler(pay_wallet, pattern=r'^pay_wallet_\d+$'))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r'^cancel_order_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_approve, pattern=r'^admin_ok_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_reject, pattern=r'^admin_no_\d+$'))

    app.add_handler(CallbackQueryHandler(wallet_menu, pattern='^wallet$'))
    app.add_handler(CallbackQueryHandler(wallet_charge_preset, pattern=r'^wchg_\d+$'))
    app.add_handler(CallbackQueryHandler(wallet_check, pattern=r'^wchk_'))

    app.add_handler(CallbackQueryHandler(store_menu, pattern='^store$'))
    app.add_handler(CallbackQueryHandler(sens_menu, pattern='^sens$'))
    app.add_handler(CallbackQueryHandler(my_orders, pattern='^my_orders$'))
    app.add_handler(CallbackQueryHandler(my_account, pattern='^my_account$'))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logging.info("Atomic Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
