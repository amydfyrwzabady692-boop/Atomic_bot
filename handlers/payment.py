"""پرداخت سفارش جم: زرین‌پال، کارت‌به‌کارت، کیف پول + تایید ادمین."""
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

from keyboards import (
    zarinpal_pay_keyboard, card_payment_keyboard, receipt_skip_keyboard,
    admin_card_keyboard, main_menu, pay_method_keyboard,
)
from db import (
    get_order, set_order_authority, update_order_status, fulfill_order,
    wallet_spend, get_or_create_user, set_order_payment_method,
)
from payments import request_payment, verify_payment

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

CARD_NUMBER = (
    os.getenv('CARD_TRANSFER_NUMBER')
    or os.getenv('CARD_NUMBER')
    or ''
).strip()
CARD_HOLDER = (
    os.getenv('CARD_TRANSFER_HOLDER')
    or os.getenv('CARD_HOLDER')
    or ''
).strip()
CARD_BANK = (
    os.getenv('CARD_TRANSFER_BANK')
    or os.getenv('CARD_BANK')
    or ''
).strip()
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID', '').strip()
_cb = (os.getenv('PAYMENT_CALLBACK_BASE') or '').strip().rstrip('/')
if not _cb:
    _domain = (os.getenv('BOT_DOMAIN') or 'botatomic.atomicshop.ir').strip()
    _cb = _domain if _domain.startswith('http') else f'https://{_domain}'
CALLBACK_BASE = _cb.rstrip('/')

WAIT_RECEIPT = 0

VPN_WARNING = (
    "⚠️ *مهم:* قبل از ورود به درگاه، *VPN را خاموش* کن.\n"
    "اگر VPN روشن باشد پرداخت معمولاً ناموفق می‌شود.\n"
)


def _card_pretty(num):
    return ' '.join(num[i:i + 4] for i in range(0, len(num), 4)) if num else '—'


def _pending(ctx, order_id=None):
    p = ctx.user_data.get('pending_order') or {}
    if order_id and p.get('order_id') != order_id:
        order = get_order(order_id)
        if not order:
            return None
        p = {
            'order_id': order[0],
            'total': order[2],
            'tg_id': int(order[6]) if order[6] else None,
        }
        ctx.user_data['pending_order'] = p
    return p


async def _notify_user(bot, tg_id, text):
    if not tg_id:
        return
    try:
        await bot.send_message(chat_id=int(tg_id), text=text, parse_mode='Markdown',
                               reply_markup=main_menu())
    except Exception:
        pass


async def start_zarinpal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split('_')[-1])
    pending = _pending(ctx, order_id) or {
        'order_id': order_id,
        'tg_id': update.effective_user.id,
    }
    order = get_order(order_id)
    if not order or order[3] not in ('pending',):
        await query.edit_message_text("❌ این سفارش قابل پرداخت نیست.")
        return

    total = order[2]
    pending['total'] = total
    if not CALLBACK_BASE:
        await query.edit_message_text(
            "❌ آدرس callback درگاه تنظیم نشده.\n"
            "ادمین باید `PAYMENT_CALLBACK_BASE` را در سرور ست کند."
        )
        return

    from payments import _merchant
    if not _merchant():
        await query.edit_message_text(
            "❌ مرچنت زرین‌پال تنظیم نشده. فعلاً از کارت‌به‌کارت استفاده کن."
        )
        return

    callback_url = f"{CALLBACK_BASE}/payment/callback?order={order_id}"
    authority, pay_url = request_payment(
        total,
        f"Atomic Bot — سفارش #{order_id}",
        callback_url,
    )
    if not authority or not pay_url:
        await query.edit_message_text(
            "❌ اتصال به درگاه زرین‌پال ممکن نشد.\n"
            "VPN را خاموش کن و دوباره تلاش کن، یا کارت‌به‌کارت را انتخاب کن."
        )
        return

    set_order_authority(order_id, authority, payment_method='zarinpal')
    pending['authority'] = authority
    pending['order_id'] = order_id
    ctx.user_data['pending_order'] = pending

    text = (
        f"💳 *پرداخت زرین‌پال — سفارش #{order_id}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 مبلغ: *{total:,} تومان*\n\n"
        f"{VPN_WARNING}\n"
        f"1️⃣ VPN را خاموش کن\n"
        f"2️⃣ روی «ورود به درگاه» بزن و پرداخت کن\n"
        f"3️⃣ بعد از برگشت، «پرداخت کردم» را بزن تا سفارش تایید شود"
    )
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=zarinpal_pay_keyboard(order_id, pay_url)
    )


async def check_zarinpal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("در حال بررسی…")
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if not order:
        await query.edit_message_text("❌ سفارش پیدا نشد.")
        return
    if order[3] in ('paid', 'delivered', 'completed', 'processing'):
        await query.edit_message_text(
            f"✅ سفارش #{order_id} قبلاً پرداخت/تحویل شده است.\nوضعیت: `{order[3]}`",
            parse_mode='Markdown',
        )
        return

    authority = order[5]
    if not authority:
        await query.edit_message_text("❌ کد درگاه برای این سفارش ثبت نشده.")
        return

    ok, ref_id = verify_payment(order[2], authority)
    if not ok:
        await query.edit_message_text(
            "⏳ هنوز پرداخت تایید نشده.\n"
            "اگر پرداخت کردی چند ثانیه صبر کن و دوباره «پرداخت کردم» را بزن.\n"
            f"{VPN_WARNING}",
            parse_mode='Markdown',
            reply_markup=zarinpal_pay_keyboard(
                order_id,
                f"https://payment.zarinpal.com/pg/StartPay/{authority}",
            ),
        )
        return

    success, status = fulfill_order(order_id)
    ctx.user_data.pop('pending_order', None)
    if success:
        msg = (
            f"✅ *پرداخت موفق — سفارش #{order_id}*\n"
            f"کد پیگیری: `{ref_id}`\n"
        )
        if status == 'delivered':
            msg += "💎 جم به‌صورت خودکار به اکانتت واریز شد."
        else:
            msg += "سفارش ثبت شد و در حال پردازش است."
        await query.edit_message_text(msg, parse_mode='Markdown')
        await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    else:
        await query.edit_message_text(
            f"⚠️ پرداخت ثبت شد ولی تحویل خودکار کامل نشد.\n"
            f"سفارش #{order_id} — پشتیبانی پیگیری می‌کند.\n({status})",
            reply_markup=main_menu(),
        )


async def start_card(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if not order or order[3] != 'pending':
        await query.edit_message_text("❌ این سفارش قابل پرداخت نیست.")
        return

    set_order_payment_method(order_id, 'card_transfer')

    total = order[2]
    bank = f"🏦 بانک: *{CARD_BANK}*\n" if CARD_BANK else ""
    text = (
        f"🏧 *پرداخت کارت‌به‌کارت — سفارش #{order_id}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 مبلغ دقیق: *{total:,} تومان*\n\n"
        f"🔢 شماره کارت:\n`{_card_pretty(CARD_NUMBER)}`\n"
        f"👤 به نام: *{CARD_HOLDER or '—'}*\n"
        f"{bank}\n"
        f"1️⃣ مبلغ را *دقیق* واریز کن\n"
        f"2️⃣ دکمه «پرداخت کردم» را بزن و *عکس رسید* بفرست\n"
        f"3️⃣ بعد از تایید ادمین، جم واریز می‌شود\n\n"
        f"_(روی شماره کارت بزن تا کپی شود)_"
    )
    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': total,
        'tg_id': update.effective_user.id,
    }
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=card_payment_keyboard(order_id)
    )


async def pay_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if not order or order[3] != 'pending':
        await query.edit_message_text("❌ این سفارش قابل پرداخت نیست.")
        return

    user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    ok, new_bal = wallet_spend(db_id, order[2], desc=f'خرید جم سفارش #{order_id}')
    if not ok:
        await query.edit_message_text(
            f"❌ موجودی کیف پول کافی نیست.\nموجودی: {new_bal:,} ت — مبلغ سفارش: {order[2]:,} ت",
            reply_markup=pay_method_keyboard(order_id, can_wallet=False),
        )
        return

    set_order_payment_method(order_id, 'wallet')

    success, status = fulfill_order(order_id)
    ctx.user_data.pop('pending_order', None)
    if success and status == 'delivered':
        msg = (
            f"✅ *پرداخت از کیف پول موفق*\n"
            f"سفارش #{order_id}\n"
            f"💎 جم واریز شد.\n"
            f"👛 موجودی جدید: *{new_bal:,} تومان*"
        )
    else:
        msg = (
            f"✅ پرداخت از کیف پول ثبت شد (سفارش #{order_id}).\n"
            f"وضعیت تحویل: `{status}`\n"
            f"👛 موجودی جدید: *{new_bal:,} تومان*"
        )
    await query.edit_message_text(msg, parse_mode='Markdown')
    await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())


async def paid_claim_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if not order or order[3] != 'pending':
        await query.edit_message_text("❌ سفارشی برای ارسال رسید پیدا نشد.")
        return ConversationHandler.END
    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': order[2],
        'tg_id': update.effective_user.id,
    }
    await query.edit_message_text(
        "🧾 *ارسال رسید پرداخت*\n"
        "━━━━━━━━━━━━━━━\n"
        "عکس رسید یا کد پیگیری را همین‌جا بفرست.",
        parse_mode='Markdown',
        reply_markup=receipt_skip_keyboard(order_id),
    )
    return WAIT_RECEIPT


async def _finalize_card(update, ctx, receipt_msg=None, via_query=None):
    pending = ctx.user_data.get('pending_order')
    if not pending:
        return ConversationHandler.END
    order_id = pending['order_id']
    user = update.effective_user

    if ADMIN_CHAT_ID:
        try:
            uname = f"@{user.username}" if user.username else "—"
            await ctx.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=(
                    f"🆕 رسید کارت‌به‌کارت — سفارش #{order_id}\n"
                    f"مبلغ: {pending['total']:,} ت\n"
                    f"کاربر: {user.full_name} ({uname})\n"
                    f"تلگرام: `{user.id}`\n\n"
                    f"پس از بررسی، تایید یا رد کن:"
                ),
                parse_mode='Markdown',
                reply_markup=admin_card_keyboard(order_id),
            )
            if receipt_msg:
                await ctx.bot.copy_message(
                    chat_id=int(ADMIN_CHAT_ID),
                    from_chat_id=receipt_msg.chat_id,
                    message_id=receipt_msg.message_id,
                )
        except Exception:
            pass

    text = (
        "✅ *رسید دریافت شد*\n"
        "━━━━━━━━━━━━━━━\n"
        f"سفارش #{order_id} در صف تایید ادمین است.\n"
        "بعد از تایید، جم واریز می‌شود و همین‌جا خبر می‌دهیم."
    )
    if via_query:
        await via_query.edit_message_text(text, parse_mode='Markdown')
        await via_query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
    return ConversationHandler.END


async def receive_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _finalize_card(update, ctx, receipt_msg=update.message)


async def skip_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await _finalize_card(update, ctx, receipt_msg=None, via_query=query)


async def cancel_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("انصراف")
    parts = query.data.split('_')
    order_id = int(parts[-1]) if parts[-1].isdigit() else None
    pending = ctx.user_data.pop('pending_order', None)
    oid = order_id or (pending or {}).get('order_id')
    if oid:
        order = get_order(oid)
        if order and order[3] == 'pending':
            update_order_status(oid, 'canceled')
    await query.edit_message_text("❌ پرداخت لغو شد.")
    await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    return ConversationHandler.END


async def admin_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ADMIN_CHAT_ID or str(update.effective_user.id) != str(ADMIN_CHAT_ID):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if not order:
        await query.edit_message_text("❌ سفارش پیدا نشد.")
        return
    if order[3] in ('delivered', 'completed'):
        await query.edit_message_text(f"سفارش #{order_id} قبلاً تحویل شده.")
        return

    success, status = fulfill_order(order_id)
    tg_id = order[6]
    if success:
        await query.edit_message_text(f"✅ سفارش #{order_id} تایید و پردازش شد ({status}).")
        await _notify_user(
            ctx.bot,
            tg_id,
            f"✅ سفارش #{order_id} تایید شد.\n"
            + ("💎 جم به اکانتت واریز شد." if status == 'delivered' else "در حال پردازش است."),
        )
    else:
        await query.edit_message_text(f"⚠️ تایید شد ولی تحویل کامل نشد: {status}")
        await _notify_user(
            ctx.bot,
            tg_id,
            f"⚠️ سفارش #{order_id} تایید شد ولی تحویل خودکار کامل نشد. پشتیبانی پیگیری می‌کند.",
        )


async def admin_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ADMIN_CHAT_ID or str(update.effective_user.id) != str(ADMIN_CHAT_ID):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if order and order[3] == 'pending':
        update_order_status(order_id, 'canceled')
    await query.edit_message_text(f"❌ سفارش #{order_id} رد شد.")
    if order and order[6]:
        await _notify_user(
            ctx.bot,
            order[6],
            f"❌ سفارش #{order_id} رد شد.\nاگر مبلغی واریز کردی با پشتیبانی در ارتباط باش.",
        )


def payment_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(paid_claim_start, pattern=r'^paid_done_\d+$')],
        states={
            WAIT_RECEIPT: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL | (filters.TEXT & ~filters.COMMAND)),
                    receive_receipt,
                ),
                CallbackQueryHandler(skip_receipt, pattern=r'^paid_skip_\d+$'),
                CallbackQueryHandler(cancel_order, pattern=r'^cancel_order_\d+$'),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel_order, pattern=r'^cancel_order_\d+$')],
        per_message=False,
        allow_reentry=True,
    )


# برای callback HTTP زرین‌پال
async def process_zarinpal_callback(bot, order_id, authority, status_ok):
    order = get_order(order_id)
    if not order:
        return False, 'order not found'
    if order[3] in ('paid', 'delivered', 'completed', 'processing'):
        return True, 'already processed'
    if not status_ok:
        return False, 'user canceled'
    auth = authority or order[5]
    if order[5] and authority and order[5] != authority:
        return False, 'authority mismatch'
    ok, ref_id = verify_payment(order[2], auth)
    if not ok:
        return False, 'verify failed'
    success, st = fulfill_order(order_id)
    tg_id = order[6]
    if success:
        await _notify_user(
            bot,
            tg_id,
            f"✅ پرداخت زرین‌پال موفق — سفارش #{order_id}\n"
            f"کد پیگیری: `{ref_id}`\n"
            + ("💎 جم واریز شد." if st == 'delivered' else "سفارش در حال پردازش است."),
        )
    return success, st
