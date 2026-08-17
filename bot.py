import logging
import os
import asyncio
import weakref
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

import button_style  # noqa: F401 — قبل از ساخت دکمه‌ها
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    BaseUpdateProcessor, CallbackQueryHandler, TypeHandler, filters,
)

from handlers.start import start_handler, help_handler, home_callback, myid_handler
from handlers.store import store_menu, show_category, show_product
from handlers.gems import gems_by_id_menu, show_gem, gem_conversation_handler
from handlers.gem_credentials import (
    credential_conversation_handler, credential_products_menu,
    freefire_products_menu, show_credential_product,
)
from handlers.sensitivity import sens_menu, sens_pc_menu, sens_mobile_menu, sens_buy
from handlers.cart import show_cart
from handlers.payment import (
    payment_conversation_handler, start_zarinpal, check_zarinpal,
    start_card, pay_wallet, cancel_order, change_payment_method,
    admin_approve, admin_reject,
    admin_review_order_prompt, admin_review_order_back,
)
from handlers.wallet import (
    wallet_menu, wallet_charge_preset, wallet_check, wallet_conversation_handler,
    wallet_pay_zarinpal, wallet_pay_card, admin_wallet_card_ok, admin_wallet_card_no,
    admin_wallet_review_prompt, admin_wallet_review_back,
)
from handlers.account import my_account, my_orders
from handlers.support import support_conversation_handler
from handlers.kyc import (
    kyc_conversation_handler, admin_kyc_approve, admin_kyc_reject, pay_back_methods,
)
from handlers.admin import (
    admin_cmd, admin_home_cb, admin_users, admin_users_with_balance,
    admin_user_card, admin_user_cmd, admin_block_toggle, admin_failed,
    admin_open_orders, admin_retry, admin_tickets, admin_ticket_close,
    admin_user_orders, admin_conversation_handler, admin_wallet_empty,
    admin_mark_done, admin_stuck_cancel, admin_credential_tickets,
)
from handlers.admin_extended import (
    admin_ext_router, admin_extended_conversation_handler, credadmin_cmd,
)
from handlers.premium_admin import (
    premium_admin_conversation_handler, studio_cmd, studio_router,
)
from handlers.appearance import appearance_conversation_handler, appear_router
from handlers.forced_join import force_join_guard
from handlers.site_receipts import (
    site_approve, site_reject, site_review_back, site_review_prompt,
)
from handlers.site_panel import site_panel_router
from admin_notify import is_admin, notify_admin
from refund_notify import notify_g2_refund
from db import (
    is_user_blocked, ensure_admin_schema, list_processing_auto_orders,
    fulfill_order, list_unnotified_auto_deliveries, mark_delivery_notified,
    list_unnotified_refunds, close_orders_already_refunded,
    list_expired_unpaid_orders,
    expire_order_and_refund, record_order_payment_verified,
    log_payment_attempt,
    admin_operations_snapshot, get_bool_setting, get_setting,
    open_db_pool, close_db_pool,
)
from payments import verify_payment_detailed
from webapp import start_web_server
from keyboards import main_menu
import appearance

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
# httpx آدرس کامل Telegram Bot API را لاگ می‌کند و آن URL شامل توکن است.
logging.getLogger('httpx').setLevel(logging.WARNING)

ADMIN_ALERT_INTERVAL_SECONDS = 8 * 60 * 60

MENU_ACTIONS = {
    'ff': freefire_products_menu,
    'wallet': wallet_menu,
    'orders': my_orders,
    'account': my_account,
    'store': store_menu,
    'sense': sens_menu,
    'cart': show_cart,
}


class PerUserUpdateProcessor(BaseUpdateProcessor):
    """Run different users concurrently while keeping each user's flow ordered."""

    def __init__(self, max_concurrent_updates=32):
        super().__init__(max_concurrent_updates)
        self._locks = weakref.WeakValueDictionary()

    async def initialize(self):
        return None

    async def shutdown(self):
        self._locks.clear()

    async def do_process_update(self, update, coroutine):
        user = getattr(update, 'effective_user', None)
        chat = getattr(update, 'effective_chat', None)
        key = (
            ('user', int(user.id)) if user is not None
            else ('chat', int(chat.id)) if chat is not None
            else ('update', id(update))
        )
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            await coroutine


async def text_router(update, ctx):
    # MessageHandler also sees channel posts/edited messages through
    # effective_message. The customer menu is strictly a private-chat flow.
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if (
        message is None or user is None or chat is None
        or chat.type != ChatType.PRIVATE or not isinstance(message.text, str)
    ):
        return
    blocked, admin = await asyncio.gather(
        asyncio.to_thread(is_user_blocked, user.id),
        asyncio.to_thread(is_admin, user.id),
    )
    if blocked and not admin:
        await message.reply_text(
            "🚫 حساب شما بلاک شده است.\nبرای پیگیری از طریق پشتیبانی سایت اقدام کن."
        )
        return

    # اگر ادمین در حالت پاسخ/جستجو نیست، منوی عادی
    action = appearance.menu_action(message.text)
    handler = MENU_ACTIONS.get(action) if action else None
    if handler:
        await handler(update, ctx)
    else:
        await message.reply_text("❓ متوجه نشدم. از منوی پایین انتخاب کن 👇")


async def error_handler(update, ctx):
    """Log unexpected failures and leave the user with a usable response."""
    log = logging.getLogger(__name__)
    error = ctx.error
    # «Message is not modified» خطای امن تلگرام است که دکمه‌های رفرش/بروزرسانی
    # پنل ادمین (که همان متن قبلی را دوباره edit می‌کنند) ایجاد می‌کنند. اعلان
    # «خطای داکر» به ادمین نباید برای این مورد ارسال شود؛ فقط لاگ debug کافی است.
    err_text = str(
        getattr(error, 'message', '')
        or getattr(error, 'description', '')
        or error
    )
    if 'message is not modified' in err_text.lower():
        log.debug('Benign "Message is not modified" error ignored: %s', err_text)
        return
    log.error(
        'Unhandled update error',
        exc_info=(type(error), error, error.__traceback__),
    )
    try:
        message = getattr(update, 'effective_message', None)
        chat = getattr(update, 'effective_chat', None)
        if message and chat and chat.type == ChatType.PRIVATE:
            await message.reply_text(
                "⚠️ خطای موقتی رخ داد. دوباره تلاش کن؛ اگر تکرار شد با پشتیبانی تماس بگیر.",
                reply_markup=main_menu(),
            )
    except Exception:
        log.exception('Could not send user-facing error message')
    try:
        await notify_admin(
            ctx.bot,
            '⚠️ خطای کنترل‌نشده در ربات ثبت شد. جزئیات کامل در لاگ Docker موجود است.',
            parse_mode=None,
        )
    except Exception:
        log.exception('Could not notify admins about unhandled error')


async def post_init(app):
    # Database and schema readiness are mandatory.  A bot that accepts money
    # with a partially migrated schema is less safe than a visible startup
    # failure, so these exceptions must stop startup.
    await asyncio.to_thread(open_db_pool)
    await asyncio.to_thread(ensure_admin_schema)
    from db import sync_gem_prices
    try:
        await asyncio.to_thread(sync_gem_prices)
    except Exception as e:
        logging.getLogger(__name__).warning('Gem price synchronization failed: %s', e)
    await start_web_server(app)
    app.bot_data['_g2_reconcile_task'] = asyncio.create_task(
        _g2_reconcile_loop(app), name='g2bulk-reconcile'
    )
    app.bot_data['_payment_expiry_task'] = asyncio.create_task(
        _payment_expiry_loop(app), name='payment-expiry'
    )
    app.bot_data['_admin_alert_task'] = asyncio.create_task(
        _admin_alert_loop(app), name='admin-alerts'
    )
    app.bot_data['_price_sync_task'] = asyncio.create_task(
        _price_sync_loop(app), name='gem-price-sync'
    )
    _log_startup_checks()


async def post_shutdown(app):
    for key in (
        '_g2_reconcile_task', '_payment_expiry_task', '_admin_alert_task',
        '_price_sync_task',
    ):
        task = app.bot_data.pop(key, None)
        if not task:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await asyncio.to_thread(close_db_pool)


async def _g2_reconcile_loop(app):
    """سفارش‌های PENDING سرویس تأمین را بدون ثبت سفارش دوباره پیگیری می‌کند."""
    while True:
        try:
            closed = await asyncio.to_thread(close_orders_already_refunded, 50)
            if closed:
                logging.getLogger(__name__).info(
                    'Closed already-refunded open orders: %s', closed,
                )

            order_ids = await asyncio.to_thread(list_processing_auto_orders, 50)
            for order_id in order_ids:
                success, status = await asyncio.to_thread(fulfill_order, order_id)
                if (not success) and str(status).startswith('refunded:'):
                    try:
                        amount = int(str(status).split(':', 1)[1])
                    except (TypeError, ValueError):
                        amount = 0
                    logging.getLogger(__name__).info(
                        'Order %s auto-refunded during reconcile: %s',
                        order_id, status,
                    )
                    await notify_g2_refund(app.bot, order_id, amount=amount)

            pending_notifications = await asyncio.to_thread(
                list_unnotified_auto_deliveries, 50
            )
            for order_id, telegram_id, user_done, admin_done in pending_notifications:
                if not user_done:
                    if telegram_id:
                        try:
                            await app.bot.send_message(
                                chat_id=int(telegram_id),
                                text=(
                                    f"✅ سفارش #{order_id} با موفقیت انجام شد.\n"
                                    "💎 جم توسط سرویس تأمین به اکانتت واریز شد."
                                ),
                            )
                            await asyncio.to_thread(
                                mark_delivery_notified, order_id, 'user'
                            )
                        except Exception:
                            logging.getLogger(__name__).exception(
                                'Could not notify user for completed G2Bulk order %s',
                                order_id,
                            )
                    else:
                        await asyncio.to_thread(
                            mark_delivery_notified, order_id, 'user'
                        )
                if not admin_done:
                    try:
                        admin_sent = await notify_admin(
                            app.bot,
                            (
                                f"✅ سفارش #{order_id} در G2Bulk تکمیل و در ربات "
                                "تحویل‌شده ثبت شد."
                            ),
                            parse_mode=None,
                        )
                        if admin_sent:
                            await asyncio.to_thread(
                                mark_delivery_notified, order_id, 'admin'
                            )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            'Could not notify admin for completed G2Bulk order %s',
                            order_id,
                        )

            pending_refunds = await asyncio.to_thread(list_unnotified_refunds, 50)
            for order_id, telegram_id, user_done, admin_done, refunded in pending_refunds:
                if not user_done or not admin_done:
                    await notify_g2_refund(
                        app.bot,
                        order_id,
                        telegram_id=telegram_id,
                        amount=int(refunded or 0),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception('G2Bulk reconciliation failed')
        await asyncio.sleep(5)


async def _admin_alert_loop(app):
    """هشدار دوره‌ای و ضداسپم برای مواردی که مدیر باید سریع ببیند."""
    last_signature = None
    await asyncio.sleep(60)
    while True:
        try:
            if get_bool_setting('admin_alerts_enabled', True):
                try:
                    threshold = max(
                        0, min(int(get_setting('low_stock_threshold', '5') or 5), 10_000)
                    )
                except (TypeError, ValueError):
                    threshold = 5
                ops = await asyncio.to_thread(admin_operations_snapshot, threshold)
                signature = (
                    ops['pending_receipts'], ops['stuck_processing'],
                    ops['failed_payments_24h'], ops['open_tickets'],
                    ops['low_gem_stock'], ops['low_store_stock'],
                )
                alert_total = sum(signature)
                if alert_total and signature != last_signature:
                    await notify_admin(
                        app.bot,
                        (
                            '🚨 *هشدار مرکز عملیات*\n'
                            '━━━━━━━━━━━━━━━\n'
                            f'رسید منتظر: *{ops["pending_receipts"]:,}*\n'
                            f'سفارش گیرکرده: *{ops["stuck_processing"]:,}*\n'
                            f'خطای پرداخت ۲۴ ساعت: *{ops["failed_payments_24h"]:,}*\n'
                            f'تیکت باز: *{ops["open_tickets"]:,}*\n'
                            f'موجودی کم: *{ops["low_gem_stock"] + ops["low_store_stock"]:,}*'
                        ),
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                '🚨 باز کردن مرکز عملیات', callback_data='admx_ops'
                            )
                        ]]),
                    )
                last_signature = signature
            else:
                last_signature = None
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception('Admin alert loop failed')
        await asyncio.sleep(ADMIN_ALERT_INTERVAL_SECONDS)


async def _payment_expiry_loop(app):
    """لغو سفارش منقضی؛ پرداخت زرین‌پال پیش از لغو دوباره از بانک پرسیده می‌شود."""
    while True:
        try:
            rows = await asyncio.to_thread(list_expired_unpaid_orders, 50)
            for order_id, method, authority, expected, telegram_id in rows:
                if method == 'zarinpal' and authority and expected:
                    verify_status, ref_id = await asyncio.to_thread(
                        verify_payment_detailed, expected, authority
                    )
                    if verify_status == 'unavailable':
                        continue
                    if verify_status == 'verified':
                        recorded, record_status = await asyncio.to_thread(
                            record_order_payment_verified,
                            order_id, 'zarinpal', expected,
                            authority, ref_id,
                        )
                        if recorded:
                            if record_status == 'verified':
                                await asyncio.to_thread(
                                    log_payment_attempt,
                                    provider='zarinpal', event='expiry_verified',
                                    status='success', amount=expected,
                                    order_id=order_id, authority=authority,
                                    ref_id=ref_id, telegram_id=telegram_id,
                                )
                            success, delivery = await asyncio.to_thread(
                                fulfill_order, order_id
                            )
                            if telegram_id:
                                text = (
                                    f'✅ پرداخت سفارش #{order_id} در بررسی نهایی '
                                    f'تأیید شد. وضعیت تحویل: {delivery}'
                                    if success else
                                    f'⚠️ پرداخت سفارش #{order_id} تأیید شد؛ '
                                    'پشتیبانی تحویل را پیگیری می‌کند.'
                                )
                                try:
                                    await app.bot.send_message(
                                        chat_id=int(telegram_id), text=text
                                    )
                                except Exception:
                                    logging.getLogger(__name__).exception(
                                        'Could not notify user about expiry verification order=%s',
                                        order_id,
                                    )
                        continue
                    if verify_status != 'not_paid':
                        continue
                canceled, refunded, _error = await asyncio.to_thread(
                    expire_order_and_refund, order_id
                )
                if not canceled:
                    continue
                await asyncio.to_thread(
                    log_payment_attempt,
                    provider=method or 'none', event='order_expired',
                    status='canceled', amount=expected,
                    order_id=order_id, authority=authority,
                    telegram_id=telegram_id,
                    message=f'payment timeout; wallet refund={refunded}',
                )
                if telegram_id:
                    refund_text = (
                        f'\n💰 {refunded:,} تومان به کیف پول برگشت.'
                        if refunded else ''
                    )
                    try:
                        await app.bot.send_message(
                            chat_id=int(telegram_id),
                            text=(
                                f'⌛ مهلت پرداخت سفارش #{order_id} تمام شد و '
                                f'سفارش خودکار لغو شد.{refund_text}'
                            ),
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            'Could not notify user about expired order=%s', order_id
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception('Payment expiry sweep failed')
        await asyncio.sleep(30)


async def _price_sync_loop(app):
    """به‌روزرسانی خودکار قیمت بسته‌های جم هر ۲۴ ساعت.

    هر ۲۴ ساعت نرخ زنده دلار و کاتالوگ G2Bulk را می‌گیرد. جم با آیدی با سود
    تنظیم‌شده خودش و محصولات اطلاعاتی با سود مستقل (پیش‌فرض ۴۰٪) محاسبه می‌شوند.
    """
    log = logging.getLogger(__name__)
    # اولین اجرا بلافاصله (نرخ لحظه‌ای) + سپس هر ۲۴ ساعت
    try:
        from db import sync_gem_prices_daily
        updated = await asyncio.to_thread(sync_gem_prices_daily, True)
        if updated:
            log.info('Gem price sync: %d products updated', updated)
    except Exception:
        log.exception('Gem price sync (initial) failed')
    while True:
        try:
            await asyncio.sleep(24 * 3600)
            updated = await asyncio.to_thread(sync_gem_prices_daily, True)
            if updated:
                log.info('Gem price sync: %d products updated', updated)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Gem price sync failed')
            await asyncio.sleep(5 * 60)


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
    try:
        from credential_vault import is_configured as credential_vault_configured
        if credential_vault_configured():
            if (os.getenv('ACCOUNT_CREDENTIALS_KEY') or '').strip():
                log.info('Account credentials: available (optional encryption key set)')
            else:
                log.info('Account credentials: available (plain storage)')
    except Exception as exc:
        log.error('Credential vault check failed: %s', exc)
        ok = False
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

    app = (
        ApplicationBuilder()
        .token(token)
        .connection_pool_size(32)
        .get_updates_connection_pool_size(4)
        .pool_timeout(10)
        .concurrent_updates(PerUserUpdateProcessor(32))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_error_handler(error_handler)

    # پیش از همهٔ مسیرها، عضویت کاربران عادی در کانال‌های اجباری بررسی می‌شود.
    app.add_handler(TypeHandler(Update, force_join_guard), group=-1)

    app.add_handler(CommandHandler('start', start_handler))
    app.add_handler(CommandHandler('help', help_handler))
    app.add_handler(CommandHandler('myid', myid_handler))
    app.add_handler(CommandHandler('admin', admin_cmd))
    app.add_handler(CommandHandler('credadmin', credadmin_cmd))
    app.add_handler(CommandHandler('studio', studio_cmd))
    app.add_handler(MessageHandler(filters.Regex(r'^/u_\d+$'), admin_user_cmd))

    app.add_handler(gem_conversation_handler())
    app.add_handler(credential_conversation_handler())
    app.add_handler(payment_conversation_handler())
    app.add_handler(wallet_conversation_handler())
    app.add_handler(support_conversation_handler())
    app.add_handler(kyc_conversation_handler())
    app.add_handler(admin_conversation_handler())
    app.add_handler(admin_extended_conversation_handler())
    app.add_handler(premium_admin_conversation_handler())
    app.add_handler(appearance_conversation_handler())

    app.add_handler(CallbackQueryHandler(home_callback, pattern='^home$'))
    app.add_handler(CallbackQueryHandler(freefire_products_menu, pattern=r'^gems$'))
    app.add_handler(CallbackQueryHandler(
        gems_by_id_menu, pattern=r'^(?:gems_by_id|gems_page_\d+)$'
    ))
    app.add_handler(CallbackQueryHandler(
        credential_products_menu, pattern=r'^gems_credentials$'
    ))
    app.add_handler(CallbackQueryHandler(
        show_credential_product, pattern=r'^cred_product_\d+$'
    ))
    app.add_handler(CallbackQueryHandler(show_gem, pattern=r'^gem_\d+$'))
    app.add_handler(CallbackQueryHandler(show_gem, pattern='^noop$'))

    app.add_handler(CallbackQueryHandler(start_zarinpal, pattern=r'^pay_zp_\d+$'))
    app.add_handler(CallbackQueryHandler(check_zarinpal, pattern=r'^zp_check_\d+$'))
    app.add_handler(CallbackQueryHandler(start_card, pattern=r'^pay_card_\d+$'))
    app.add_handler(CallbackQueryHandler(pay_wallet, pattern=r'^pay_wallet_\d+$'))
    app.add_handler(CallbackQueryHandler(change_payment_method, pattern=r'^change_pay_\d+$'))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r'^cancel_order_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_approve, pattern=r'^admin_ok_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_reject, pattern=r'^admin_no_\d+$'))
    app.add_handler(CallbackQueryHandler(
        admin_review_order_prompt, pattern=r'^admin_review_(?:ok|no)_\d+$'
    ))
    app.add_handler(CallbackQueryHandler(
        admin_review_order_back, pattern=r'^admin_review_back_\d+$'
    ))
    app.add_handler(CallbackQueryHandler(
        site_review_prompt, pattern=r'^site_review_(?:ok|no)_\d+$'
    ))
    app.add_handler(CallbackQueryHandler(
        site_review_back, pattern=r'^site_review_back_\d+$'
    ))
    app.add_handler(CallbackQueryHandler(site_approve, pattern=r'^site_ok_\d+$'))
    app.add_handler(CallbackQueryHandler(site_reject, pattern=r'^site_no_\d+$'))

    app.add_handler(CallbackQueryHandler(wallet_menu, pattern='^wallet$'))
    app.add_handler(CallbackQueryHandler(wallet_charge_preset, pattern=r'^wchg_\d+$'))
    app.add_handler(CallbackQueryHandler(wallet_pay_zarinpal, pattern=r'^wpay_zp_\d+$'))
    app.add_handler(CallbackQueryHandler(wallet_pay_card, pattern=r'^wpay_card_\d+$'))
    app.add_handler(CallbackQueryHandler(wallet_check, pattern=r'^wchk_'))
    app.add_handler(CallbackQueryHandler(admin_wallet_card_ok, pattern=r'^wadmin_ok_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_wallet_card_no, pattern=r'^wadmin_no_\d+$'))
    app.add_handler(CallbackQueryHandler(
        admin_wallet_review_prompt, pattern=r'^wadmin_review_(?:ok|no)_\d+$'
    ))
    app.add_handler(CallbackQueryHandler(
        admin_wallet_review_back, pattern=r'^wadmin_review_back_\d+$'
    ))

    app.add_handler(CallbackQueryHandler(store_menu, pattern='^store$'))
    app.add_handler(CallbackQueryHandler(show_category, pattern=r'^storecat_\d+$'))
    app.add_handler(CallbackQueryHandler(show_product, pattern=r'^storeprod_\d+$'))
    app.add_handler(CallbackQueryHandler(sens_menu, pattern='^sens$'))
    app.add_handler(CallbackQueryHandler(sens_pc_menu, pattern='^sens_pc$'))
    app.add_handler(CallbackQueryHandler(sens_mobile_menu, pattern='^sens_mobile$'))
    app.add_handler(CallbackQueryHandler(sens_buy, pattern=r'^sens_buy_\d+$'))
    app.add_handler(CallbackQueryHandler(my_orders, pattern='^my_orders$'))
    app.add_handler(CallbackQueryHandler(my_account, pattern='^my_account$'))

    # پنل ادمین
    app.add_handler(CallbackQueryHandler(admin_home_cb, pattern='^adm_home$'))
    app.add_handler(CallbackQueryHandler(admin_users, pattern='^adm_users$'))
    app.add_handler(CallbackQueryHandler(
        admin_users_with_balance, pattern='^adm_users_balance$'
    ))
    app.add_handler(CallbackQueryHandler(admin_user_card, pattern=r'^adm_user_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_block_toggle, pattern=r'^adm_block_[01]_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_user_orders, pattern=r'^adm_ords_\d+$'))
    app.add_handler(CallbackQueryHandler(
        admin_wallet_empty, pattern=r'^adm_wempty_(?:confirm_)?\d+$'
    ))
    app.add_handler(CallbackQueryHandler(admin_failed, pattern='^adm_failed$'))
    app.add_handler(CallbackQueryHandler(admin_open_orders, pattern='^adm_open$'))
    app.add_handler(CallbackQueryHandler(admin_retry, pattern=r'^adm_retry_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_mark_done, pattern=r'^adm_done_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_stuck_cancel, pattern=r'^adm_cancel_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_tickets, pattern='^adm_tickets$'))
    app.add_handler(CallbackQueryHandler(
        admin_credential_tickets, pattern='^adm_cred_tickets$'
    ))
    app.add_handler(CallbackQueryHandler(admin_ticket_close, pattern=r'^adm_tclose_\d+$'))
    app.add_handler(CallbackQueryHandler(
        site_panel_router, pattern=r'^admx_site(?:cred|receipt)'
    ))
    app.add_handler(CallbackQueryHandler(admin_ext_router, pattern=r'^admx_'))
    app.add_handler(
        CallbackQueryHandler(
            studio_router, pattern=r'^studio_(?:home|g2|payments)$'
        )
    )
    app.add_handler(CallbackQueryHandler(
        appear_router, pattern=r'^ap_(?:home$|h:|c:|i:|cle:|rst:)'
    ))

    app.add_handler(CallbackQueryHandler(admin_kyc_approve, pattern=r'^kyc_ok_\d+_\d+$'))
    app.add_handler(CallbackQueryHandler(admin_kyc_reject, pattern=r'^kyc_no_\d+_\d+$'))
    app.add_handler(CallbackQueryHandler(pay_back_methods, pattern=r'^pay_back_\d+$'))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        text_router,
    ))

    logging.info("Atomic Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
