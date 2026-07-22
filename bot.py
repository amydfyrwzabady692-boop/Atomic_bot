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
from handlers.sensitivity import sens_menu, sens_pc_menu, sens_mobile_menu, sens_buy
from handlers.cart import show_cart
from handlers.payment import (
    payment_conversation_handler, start_zarinpal, check_zarinpal,
    start_card, pay_wallet, cancel_order, admin_approve, admin_reject,
)
from handlers.wallet import (
    wallet_menu, wallet_charge_preset, wallet_check, wallet_conversation_handler,
)
from handlers.account import my_account, my_orders
from handlers.support import support_conversation_handler
from handlers.kyc import (
    kyc_conversation_handler, admin_kyc_approve, admin_kyc_reject, pay_back_methods,
)
from handlers.admin import (
    admin_cmd, admin_home_cb, admin_users, admin_user_card, admin_user_cmd,
    admin_block_toggle, admin_failed, admin_open_orders, admin_retry,
    admin_tickets, admin_ticket_close, admin_user_orders, admin_conversation_handler,
)
from admin_notify import is_admin
from db import is_user_blocked, ensure_admin_schema
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


async def text_router(update, ctx):
    user = update.effective_user
    if user and is_user_blocked(user.id) and not is_admin(user.id):
        await update.message.reply_text(
            "🚫 حساب شما بلاک شده است.\nبرای پیگیری از طریق پشتیبانی سایت اقدام کن."
        )
        return

    # اگر ادمین در حالت پاسخ/جستجو نیست، منوی عادی
    handler = MENU_TEXTS.get(update.message.text)
    if handler:
        await handler(update, ctx)
    else:
        await update.message.reply_text("❓ متوجه نشدم. از منوی پایین انتخاب کن 👇")


async def post_init(app):
    try:
        ensure_admin_schema()
        from db import sync_gem_prices
        sync_gem_prices()
    except Exception as e:
        logging.getLogger(__name__).warning('ensure_admin_schema/prices: %s', e)
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
        log.warning('ADMIN_CHAT_ID empty — اعلان ادمین کار نمی‌کند. /myid بزن')
    else:
        log.info('ADMIN_CHAT_ID configured')
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
    app.add_handler(CommandHandler('admin', admin_cmd))
    app.add_handler(MessageHandler(filters.Regex(r'^/u_\d+$'), admin_user_cmd))

    app.add_handler(gem_conversation_handler())
    app.add_handler(payment_conversation_handler())
    app.add_handler(wallet_conversation_handler())
    app.add_handler(support_conversation_handler())
    app.add_handler(kyc_conversation_handler())
    app.add_handler(admin_conversation_handler())

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
    app.add_handler(CallbackQueryHandler(sens_pc_menu, pattern='^sens_pc$'))
    app.add_handler(CallbackQueryHandler(sens_mobile_menu, pattern='^sens_mobile$'))
    app.add_handler(CallbackQueryHandler(sens_buy, pattern=r'^sens_buy_(basic|plus)$'))
    app.add_handler(CallbackQueryHandler(my_orders, pattern='^my_orders$'))
    app.add_handler(CallbackQueryHandler(my_account, pattern='^my_account$'))

    # پنل ادمین
    app.add_handler(CallbackQueryHandler(admin_home_cb, pattern='^adm_home$'))
    app.add_handler(CallbackQueryHandler(admin_users, pattern='^adm_users$'))
    app.add_handler(CallbackQueryHandler(admin_user_card, pattern=r'^adm_user_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_block_toggle, pattern=r'^adm_block_[01]_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_user_orders, pattern=r'^adm_ords_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_failed, pattern='^adm_failed$'))
    app.add_handler(CallbackQueryHandler(admin_open_orders, pattern='^adm_open$'))
    app.add_handler(CallbackQueryHandler(admin_retry, pattern=r'^adm_retry_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_tickets, pattern='^adm_tickets$'))
    app.add_handler(CallbackQueryHandler(admin_ticket_close, pattern=r'^adm_tclose_\d+$'))

    app.add_handler(CallbackQueryHandler(admin_kyc_approve, pattern=r'^kyc_ok_\d+_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_kyc_reject, pattern=r'^kyc_no_\d+_\d+$'))
    app.add_handler(CallbackQueryHandler(pay_back_methods, pattern=r'^pay_back_\d+$'))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logging.info("Atomic Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
