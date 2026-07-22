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
    admin_card_keyboard, main_menu, pay_method_keyboard, admin_failed_order_keyboard,
)
from payments import request_payment, verify_payment
from admin_notify import notify_admin
from db import (
    get_order, set_order_authority, update_order_status, fulfill_order,
    wallet_spend, get_or_create_user, set_order_payment_method,
    order_requires_kyc, is_kyc_approved, get_order_items,
)

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
    "⚠️ تلگرام فیلتر است؛ اول لینک را *کپی* کن، بعد VPN را خاموش کن و لینک را در مرورگر باز کن.\n"
)


async def _alert_fulfill_issue(bot, order_id, status, payment_hint=''):
    order = get_order(order_id)
    if not order:
        return
    tg = order[6] or '—'
    await notify_admin(
        bot,
        (
            f"⚠️ *مشکل تحویل سفارش #{order_id}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"وضعیت: `{status}`\n"
            f"پرداخت: {payment_hint or order[4]}\n"
            f"مبلغ: {order[2]:,} ت\n"
            f"کاربر: `{tg}`\n\n"
            f"از پنل /admin → تحویل ناموفق می‌توانی دوباره تلاش کنی."
        ),
        reply_markup=admin_failed_order_keyboard(order_id, str(tg) if tg != '—' else ''),
    )


async def _notify_sense_sale(bot, order_id):
    """بعد از خرید پک سنس — آیدی کاربر را برای ارسال در پیوی به ادمین بفرست."""
    order = get_order(order_id)
    if not order:
        return
    items = get_order_items(order_id)
    titles = '، '.join(it[1] for it in items) if items else 'پک سنس'
    tg = order[6] or '—'
    uname = '—'
    name = '—'
    try:
        from db import get_user_profile
        p = get_user_profile(telegram_id=tg)
        if p:
            name = f"{p[3] or ''} {p[4] or ''}".strip() or '—'
            un = (p[2] or '').strip()
            uname = f"@{un}" if un else '—'
    except Exception:
        pass
    text = (
        f"🎯 *خرید پک سنس — سفارش #{order_id}*\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"پک: *{titles}*\n"
        f"مبلغ: *{order[2]:,}* تومان\n"
        f"پرداخت: `{order[4]}`\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"نام: {name}\n"
        f"آیدی: *{uname}*\n"
        f"شناسه عددی:\n`{tg}`\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"برو پیوی این کاربر و پک را بفرست.\n"
        f"_(روی شناسه بزن تا کپی شود)_"
    )
    await notify_admin(bot, text)


def _success_user_text(order_id, status, ref_id=None):
    if status == 'sense_manual':
        msg = (
            f"✅ *پرداخت موفق — سفارش #{order_id}*\n"
        )
        if ref_id:
            msg += f"کد پیگیری: `{ref_id}`\n"
        msg += (
            "پک سنس ثبت شد.\n"
            "به‌زودی در *پیوی تلگرام* برات ارسال می‌شود."
        )
        return msg
    msg = f"✅ *پرداخت موفق — سفارش #{order_id}*\n"
    if ref_id:
        msg += f"کد پیگیری: `{ref_id}`\n"
    if status == 'delivered':
        msg += "💎 جم به‌صورت خودکار به اکانتت واریز شد."
    elif status == 'paid':
        msg += "سفارش ثبت شد."
    else:
        msg += "سفارش ثبت شد و در حال پردازش است."
    return msg


def _zarinpal_link_text(order_id, total, pay_url):
    return (
        f"✦ *پرداخت زرین‌پال*\n"
        f"سفارش `#{order_id}`\n"
        f"مبلغ: *{total:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"*راهنما*\n"
        f"۱ · لینک را لمس کن و *کپی* کن\n"
        f"۲ · *VPN را خاموش* کن\n"
        f"۳ · لینک را در مرورگر باز کن و پرداخت کن\n"
        f"۴ · برگرد و «پرداخت کردم» را بزن\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"🔗 *لینک پرداخت*\n"
        f"`{pay_url}`"
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

    # احراز برای بسته‌های ۱۱۸۸ و ۲۴۲۰ — فقط درگاه
    if order_requires_kyc(order_id) and not is_kyc_approved(update.effective_user.id):
        from handlers.kyc import prompt_kyc_for_order
        await prompt_kyc_for_order(query, update.effective_user, order_id)
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
    authority, pay_url, err = request_payment(
        total,
        f"Atomic Bot — سفارش #{order_id}",
        callback_url,
    )
    if not authority or not pay_url:
        await query.edit_message_text(
            "❌ ساخت لینک زرین‌پال ممکن نشد.\n"
            f"علت: `{err or 'نامشخص'}`\n\n"
            "فعلاً از *کارت‌به‌کارت* استفاده کن یا چند دقیقه بعد دوباره تلاش کن.",
            parse_mode='Markdown',
            reply_markup=pay_method_keyboard(order_id, can_wallet=False),
        )
        return

    set_order_authority(order_id, authority, payment_method='zarinpal')
    pending['authority'] = authority
    pending['order_id'] = order_id
    ctx.user_data['pending_order'] = pending

    text = _zarinpal_link_text(order_id, total, pay_url)
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
        if status == 'sense_manual':
            await _notify_sense_sale(ctx.bot, order_id)
        elif status not in ('delivered', 'paid', 'sense_manual'):
            await _alert_fulfill_issue(ctx.bot, order_id, status, 'zarinpal')
        await query.edit_message_text(
            _success_user_text(order_id, status, ref_id),
            parse_mode='Markdown',
        )
        await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    else:
        await _alert_fulfill_issue(ctx.bot, order_id, status, 'zarinpal')
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
        f"✦ *کارت‌به‌کارت*\n"
        f"سفارش `#{order_id}`\n"
        f"مبلغ دقیق: *{total:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"شماره کارت\n"
        f"`{_card_pretty(CARD_NUMBER)}`\n"
        f"به نام *{CARD_HOLDER or '—'}*\n"
        f"{bank}"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"۱ · مبلغ را *دقیق* واریز کن\n"
        f"۲ · «پرداخت کردم» را بزن و عکس رسید بفرست\n"
        f"۳ · بعد از تایید ادمین، سفارشت انجام می‌شود\n"
        f"\n_روی شماره کارت بزن تا کپی شود_"
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
    if success and status == 'sense_manual':
        await _notify_sense_sale(ctx.bot, order_id)
        msg = (
            f"✅ *پرداخت از کیف پول موفق*\n"
            f"سفارش #{order_id}\n"
            f"پک سنس ثبت شد و به‌زودی در پیوی برات ارسال می‌شود.\n"
            f"موجودی جدید: *{new_bal:,}* تومان"
        )
    elif success and status == 'delivered':
        msg = (
            f"✅ *پرداخت از کیف پول موفق*\n"
            f"سفارش #{order_id}\n"
            f"💎 جم واریز شد.\n"
            f"موجودی جدید: *{new_bal:,} تومان*"
        )
    else:
        if status not in ('paid',):
            await _alert_fulfill_issue(ctx.bot, order_id, status, 'wallet')
        msg = (
            f"✅ پرداخت از کیف پول ثبت شد (سفارش #{order_id}).\n"
            f"وضعیت: `{status}`\n"
            f"موجودی جدید: *{new_bal:,}* تومان"
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

    admin_ok = False
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
            admin_ok = True
        except Exception as e:
            print(f'[CARD] admin notify failed: {e}')

    if admin_ok:
        text = (
            "✅ *رسید دریافت شد*\n"
            "━━━━━━━━━━━━━━━\n"
            f"سفارش #{order_id} برای ادمین ارسال شد.\n"
            "بعد از تایید ادمین، سفارشت انجام می‌شود و همین‌جا خبر می‌دهیم."
        )
    else:
        text = (
            "⚠️ *رسید ذخیره شد ولی به ادمین نرسید*\n"
            "━━━━━━━━━━━━━━━\n"
            f"سفارش #{order_id}\n"
            "ادمین باید `/myid` بزند و `ADMIN_CHAT_ID` را در سرور تنظیم کند.\n"
            "تا آن زمان تایید خودکار از پنل ادمین ممکن نیست."
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
    if success and status == 'sense_manual':
        await _notify_sense_sale(ctx.bot, order_id)
        await query.edit_message_text(
            f"✅ سفارش #{order_id} تایید شد (پک سنس).\nآیدی کاربر برایت ارسال شد."
        )
        await _notify_user(
            ctx.bot,
            tg_id,
            f"✅ سفارش #{order_id} تایید شد.\nپک سنس به‌زودی در پیوی برات ارسال می‌شود.",
        )
    elif success and status in ('delivered', 'paid'):
        await query.edit_message_text(f"✅ سفارش #{order_id} تایید و پردازش شد ({status}).")
        await _notify_user(
            ctx.bot,
            tg_id,
            f"✅ سفارش #{order_id} تایید شد.\n"
            + ("💎 جم به اکانتت واریز شد." if status == 'delivered' else "سفارش ثبت شد."),
        )
    else:
        await _alert_fulfill_issue(ctx.bot, order_id, status, 'card_transfer')
        await query.edit_message_text(
            f"⚠️ تایید شد ولی تحویل کامل نشد: `{status}`\nدکمه تلاش مجدد در اعلان ادمین است.",
            parse_mode='Markdown',
            reply_markup=admin_failed_order_keyboard(order_id, str(tg_id or '')),
        )
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
        if st == 'sense_manual':
            await _notify_sense_sale(bot, order_id)
        await _notify_user(
            bot,
            tg_id,
            _success_user_text(order_id, st, ref_id),
        )
    return success, st
