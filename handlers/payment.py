"""پرداخت سفارش جم: زرین‌پال، کارت‌به‌کارت، کیف پول + تایید ادمین."""
from html import escape
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
from db import (
    get_order, set_order_authority, update_order_status, fulfill_order,
    wallet_spend, get_or_create_user, set_order_payment_method,
    order_requires_kyc, is_kyc_approved, get_order_items, get_gem_infos_for_order,
    get_order_payable, apply_wallet_to_order, get_wallet_balance, refund_order_wallet,
    get_setting, get_bool_setting, save_payment_receipt, mark_receipt_reviewed,
)
from payments import request_payment, verify_payment
from admin_notify import notify_admin, is_admin
import time

ZP_TTL_SEC = 15 * 60  # مهلت درگاه زرین‌پال
ZP_MAX_CHECKS = 10

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


def _receipt_admin_caption(order_id, pending, user, receipt_text=''):
    """کپشن کوتاه و امن برای کارت بررسی رسید ادمین."""
    order = get_order(order_id)
    items = get_order_items(order_id)
    shop = get_setting('shop_name', 'Atomic Shop') or 'Atomic Shop'
    item_lines = []
    for item in items[:5]:
        qty = int(item[3] or 1)
        item_lines.append(
            f"• {escape(str(item[1] or 'محصول')[:70])}"
            + (f" × {qty}" if qty > 1 else "")
        )
    if len(items) > 5:
        item_lines.append(f"• و {len(items) - 5} مورد دیگر")
    products = '\n'.join(item_lines) or '• محصول ثبت‌شده در سفارش'
    game_lines = []
    try:
        for info in get_gem_infos_for_order(order_id)[:3]:
            game_uid = info[2] or '—'
            player = info[3] or 'در انتظار دریافت'
            game_lines.append(
                f"🎮 آیدی بازی: <code>{escape(str(game_uid)[:50])}</code>\n"
                f"🏷 نام داخل بازی: {escape(str(player)[:80])}"
            )
    except Exception:
        # سفارش‌های فروشگاه و پک سنس طبیعتاً اطلاعات بازی ندارند.
        pass
    game_info = ('\n' + '\n'.join(game_lines)) if game_lines else ''
    username = f"@{user.username}" if user.username else "ندارد"
    total = int(order[2] if order else pending.get('total', 0))
    wallet_paid = int(order[7] if order else 0)
    card_paid = int(pending.get('total', max(0, total - wallet_paid)))
    note = (receipt_text or '').strip()
    note_line = f"\n📝 توضیح رسید: {escape(note[:180])}" if note else ''
    return (
        f"🧾 <b>بررسی پرداخت {escape(shop)}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🛍 <b>سفارش #{order_id}</b>\n"
        f"{products}\n\n"
        f"💳 مبلغ واریزی: <b>{card_paid:,} تومان</b>\n"
        f"💰 مبلغ کل: {total:,} تومان\n"
        + (f"👛 کسر از کیف پول: {wallet_paid:,} تومان\n" if wallet_paid else "")
        + f"🔖 روش پرداخت: کارت‌به‌کارت\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 کاربر: {escape(user.full_name or '—')}\n"
        f"🔗 نام کاربری: {escape(username)}\n"
        f"🆔 شناسه تلگرام: <code>{user.id}</code>"
        f"{game_info}"
        f"{note_line}\n\n"
        f"پس از تطبیق عکس رسید و مبلغ، پرداخت را تأیید یا رد کنید."
    )


async def _edit_review_message(query, text, reply_markup=None, parse_mode=None):
    """نتیجه بررسی را روی پیام متنی، عکس یا فایل رسید ثبت می‌کند."""
    if query.message and (query.message.photo or query.message.document):
        await query.edit_message_caption(
            caption=text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    else:
        await query.edit_message_text(
            text=text, parse_mode=parse_mode, reply_markup=reply_markup
        )


def _order_pay_keyboard(order_id, db_id=None):
    order = get_order(order_id)
    if not order:
        return pay_method_keyboard(order_id, can_wallet=False)
    remaining = get_order_payable(order_id)
    # همیشه از صاحب سفارش بخوان تا db_id کهنه باعث مخفی شدن دکمه نشود
    owner_id = db_id or order[1]
    bal = int(get_wallet_balance(owner_id) or 0)
    return pay_method_keyboard(
        order_id,
        can_wallet=remaining > 0,
        wallet_balance=bal,
        remaining=remaining,
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
    if not get_bool_setting('zarinpal_enabled', True):
        await query.edit_message_text(
            "❌ درگاه زرین‌پال موقتاً غیرفعال است.",
            reply_markup=_order_pay_keyboard(order_id, ctx.user_data.get('db_id')),
        )
        return

    # احراز برای بسته‌های ۱۱۸۸ و ۲۴۲۰ — فقط درگاه
    if order_requires_kyc(order_id) and not is_kyc_approved(update.effective_user.id):
        from handlers.kyc import prompt_kyc_for_order
        await prompt_kyc_for_order(query, update.effective_user, order_id)
        return

    payable = get_order_payable(order_id)
    if payable <= 0:
        success, status = fulfill_order(order_id)
        if status == 'sense_manual':
            await _notify_sense_sale(ctx.bot, order_id)
        await query.edit_message_text(
            _success_user_text(order_id, status or 'delivered'),
            parse_mode='Markdown',
        )
        await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
        return

    total = order[2]
    wallet_paid = int(order[7] or 0)
    pending['total'] = payable
    callback_base = get_setting('payment_callback_base', CALLBACK_BASE).rstrip('/')
    if not callback_base:
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

    callback_url = f"{callback_base}/payment/callback?order={order_id}"
    authority, pay_url, err = request_payment(
        payable,
        f"Atomic Bot — سفارش #{order_id}",
        callback_url,
    )
    if not authority or not pay_url:
        await query.edit_message_text(
            "❌ ساخت لینک زرین‌پال ممکن نشد.\n"
            f"علت: `{err or 'نامشخص'}`\n\n"
            "فعلاً از *کارت‌به‌کارت* استفاده کن یا چند دقیقه بعد دوباره تلاش کن.",
            parse_mode='Markdown',
            reply_markup=_order_pay_keyboard(order_id, ctx.user_data.get('db_id')),
        )
        return

    set_order_authority(order_id, authority, payment_method='zarinpal')
    pending['authority'] = authority
    pending['order_id'] = order_id
    pending['payable'] = payable
    ctx.user_data['pending_order'] = pending
    ctx.user_data.setdefault('zp_meta', {})[str(order_id)] = {
        'started': time.time(),
        'checks': 0,
        'payable': payable,
    }

    note = ""
    if wallet_paid > 0:
        note = f"از کیف پول کسر شد: *{wallet_paid:,}* ت\nباقی‌مانده: *{payable:,}* ت\n"
    text = (
        f"✦ *پرداخت زرین‌پال*\n"
        f"سفارش `#{order_id}`\n"
        f"مبلغ کل: *{total:,}* تومان\n"
        f"{note}"
        f"مبلغ درگاه: *{payable:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"*راهنما*\n"
        f"۱ · لینک را لمس کن و *کپی* کن\n"
        f"۲ · *VPN را خاموش* کن\n"
        f"۳ · در مرورگر باز کن و پرداخت کن\n"
        f"۴ · برگرد و «پرداخت کردم» را بزن\n"
        f"⏱ مهلت حدود ۱۵ دقیقه\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"🔗 *لینک پرداخت*\n"
        f"`{pay_url}`"
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

    meta = (ctx.user_data.get('zp_meta') or {}).get(str(order_id)) or {}
    started = float(meta.get('started') or time.time())
    checks = int(meta.get('checks') or 0) + 1
    payable = int(meta.get('payable') or get_order_payable(order_id) or order[2])
    meta.update({'started': started, 'checks': checks, 'payable': payable})
    ctx.user_data.setdefault('zp_meta', {})[str(order_id)] = meta
    elapsed = time.time() - started
    left = max(0, int(ZP_TTL_SEC - elapsed))

    ok, ref_id = verify_payment(payable, authority)
    if not ok:
        expired = elapsed >= ZP_TTL_SEC or checks >= ZP_MAX_CHECKS
        kb = zarinpal_pay_keyboard(
            order_id,
            f"https://payment.zarinpal.com/pg/StartPay/{authority}",
        )
        if expired:
            await query.edit_message_text(
                f"❌ *پرداخت تایید نشد*\n"
                f"سفارش `#{order_id}`\n"
                f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
                f"مهلت درگاه تمام شد یا پرداختی ثبت نشده.\n"
                f"اگر پول کم نشده، دوباره «زرین‌پال» را بزن یا *کارت‌به‌کارت* را انتخاب کن.",
                parse_mode='Markdown',
                reply_markup=_order_pay_keyboard(order_id, ctx.user_data.get('db_id')),
            )
        else:
            mins = max(1, (left + 59) // 60)
            await query.edit_message_text(
                f"⏳ هنوز پرداخت تایید نشده (بررسی {checks}/{ZP_MAX_CHECKS}).\n"
                f"اگر پرداخت کردی چند لحظه صبر کن و دوباره بزن.\n"
                f"⏱ حدود *{mins}* دقیقه از مهلت مانده.\n"
                f"اگر لینک را باز نکردی / پرداخت نکردی، صبر کن تا مهلت تمام شود یا روش دیگر را انتخاب کن.\n"
                f"{VPN_WARNING}",
                parse_mode='Markdown',
                reply_markup=kb,
            )
        return

    success, status = fulfill_order(order_id)
    ctx.user_data.pop('pending_order', None)
    (ctx.user_data.get('zp_meta') or {}).pop(str(order_id), None)
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
    if not get_bool_setting('card_transfer_enabled', True):
        await query.edit_message_text(
            "❌ کارت‌به‌کارت موقتاً غیرفعال است.",
            reply_markup=_order_pay_keyboard(order_id, ctx.user_data.get('db_id')),
        )
        return

    payable = get_order_payable(order_id)
    if payable <= 0:
        success, status = fulfill_order(order_id)
        if status == 'sense_manual':
            await _notify_sense_sale(ctx.bot, order_id)
        await query.edit_message_text(
            _success_user_text(order_id, status or 'delivered'),
            parse_mode='Markdown',
        )
        return

    set_order_payment_method(order_id, 'card_transfer')
    total = order[2]
    wallet_paid = int(order[7] or 0)
    card_number = get_setting('card_number', CARD_NUMBER)
    card_holder = get_setting('card_holder', CARD_HOLDER)
    card_bank = get_setting('card_bank', CARD_BANK)
    if not card_number:
        await query.edit_message_text("❌ شماره کارت هنوز توسط مدیر تنظیم نشده است.")
        return
    bank = f"بانک: *{card_bank}*\n" if card_bank else ""
    note = f"کسر کیف پول: *{wallet_paid:,}* ت\n" if wallet_paid else ""
    text = (
        f"✦ *کارت‌به‌کارت*\n"
        f"سفارش `#{order_id}`\n"
        f"مبلغ کل: *{total:,}* تومان\n"
        f"{note}"
        f"مبلغ واریزی: *{payable:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"شماره کارت\n"
        f"`{_card_pretty(card_number)}`\n"
        f"به نام *{card_holder or '—'}*\n"
        f"{bank}"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"۱ · مبلغ *{payable:,}* را دقیق واریز کن\n"
        f"۲ · «پرداخت کردم» را بزن و عکس رسید بفرست\n"
        f"۳ · بعد از تایید ادمین، سفارشت انجام می‌شود\n"
        f"\n_روی شماره کارت بزن تا کپی شود_"
    )
    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': payable,
        'tg_id': update.effective_user.id,
    }
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=card_payment_keyboard(order_id)
    )


async def pay_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if not order or order[3] != 'pending':
        await query.answer("❌ این سفارش قابل پرداخت نیست.", show_alert=True)
        return

    user = update.effective_user
    # همیشه کاربر را از تلگرام تازه بگیر (db_id کهنه = موجودی اشتباه)
    db_id, _ = get_or_create_user(
        user.id, user.first_name or '', user.last_name or '', user.username or ''
    )
    ctx.user_data['db_id'] = db_id

    # اگر سفارش مال کاربر دیگری است، اجازه نده
    if int(order[1]) != int(db_id):
        await query.answer("این سفارش متعلق به شما نیست.", show_alert=True)
        return

    bal = int(get_wallet_balance(db_id) or 0)
    if bal <= 0:
        await query.answer(
            "موجودی کیف پول صفر است. اول از منوی کیف پول شارژ کن.",
            show_alert=True,
        )
        await query.edit_message_text(
            f"❌ موجودی کیف پول صفر است.\n"
            f"سفارش #{order_id} هنوز باز است — اول شارژ کن، بعد دوباره پرداخت کن.",
            reply_markup=_order_pay_keyboard(order_id, db_id),
        )
        return

    await query.answer()
    ok, used, remaining, new_bal, err = apply_wallet_to_order(db_id, order_id)
    if not ok:
        await query.edit_message_text(
            f"❌ {err or 'استفاده از کیف پول ممکن نشد.'}",
            reply_markup=_order_pay_keyboard(order_id, db_id),
        )
        return

    if remaining <= 0:
        set_order_payment_method(order_id, 'wallet')
        success, status = fulfill_order(order_id)
        ctx.user_data.pop('pending_order', None)
        if success and status == 'sense_manual':
            await _notify_sense_sale(ctx.bot, order_id)
        elif success and status not in ('delivered', 'paid', 'sense_manual'):
            await _alert_fulfill_issue(ctx.bot, order_id, status, 'wallet')
        msg = _success_user_text(order_id, status if success else 'paid')
        msg += f"\nکسر از کیف پول: *{used:,}* ت\nموجودی: *{new_bal:,}* ت"
        await query.edit_message_text(msg, parse_mode='Markdown')
        await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
        return

    await query.edit_message_text(
        f"✅ *{used:,}* تومان از کیف پول کسر شد.\n"
        f"موجودی جدید: *{new_bal:,}* ت\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"باقی‌مانده سفارش: *{remaining:,}* تومان\n"
        f"روش پرداخت باقی‌مانده را انتخاب کن:",
        parse_mode='Markdown',
        reply_markup=_order_pay_keyboard(order_id, db_id),
    )


async def paid_claim_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split('_')[-1])
    order = get_order(order_id)
    if not order or order[3] != 'pending':
        await query.edit_message_text("❌ سفارشی برای ارسال رسید پیدا نشد.")
        return ConversationHandler.END
    payable = get_order_payable(order_id)
    ctx.user_data['pending_order'] = {
        'order_id': order_id,
        'total': payable,
        'tg_id': update.effective_user.id,
    }
    await query.edit_message_text(
        "🧾 *ارسال رسید پرداخت*\n"
        "━━━━━━━━━━━━━━━\n"
        "لطفاً *عکس رسید* را همین‌جا بفرست.\n"
        "در صورت نیاز می‌توانی کد پیگیری را در کپشن عکس بنویسی.",
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
    file_id = ''
    receipt_text = ''
    if receipt_msg:
        receipt_text = receipt_msg.text or receipt_msg.caption or ''
        if receipt_msg.photo:
            file_id = receipt_msg.photo[-1].file_id
        elif receipt_msg.document:
            file_id = receipt_msg.document.file_id
    try:
        save_payment_receipt(
            order_id=order_id, telegram_id=user.id, file_id=file_id, text=receipt_text
        )
    except Exception as e:
        print(f'[CARD] receipt persistence failed: {e}')

    admin_ok = False
    from admin_notify import admin_ids
    recipients = admin_ids()
    if recipients:
        try:
            caption = _receipt_admin_caption(
                order_id, pending, user, receipt_text=receipt_text
            )
            for aid in recipients:
                if receipt_msg and receipt_msg.photo:
                    await ctx.bot.send_photo(
                        chat_id=aid,
                        photo=receipt_msg.photo[-1].file_id,
                        caption=caption,
                        parse_mode='HTML',
                        reply_markup=admin_card_keyboard(order_id),
                    )
                elif receipt_msg and receipt_msg.document:
                    await ctx.bot.send_document(
                        chat_id=aid,
                        document=receipt_msg.document.file_id,
                        caption=caption,
                        parse_mode='HTML',
                        reply_markup=admin_card_keyboard(order_id),
                    )
                else:
                    await ctx.bot.send_message(
                        chat_id=aid,
                        text=caption,
                        parse_mode='HTML',
                        reply_markup=admin_card_keyboard(order_id),
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


async def receipt_photo_required(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📷 برای بررسی پرداخت، ارسال *عکس رسید* الزامی است.\n"
        "لطفاً رسید را به‌صورت عکس یا فایل تصویری بفرست.",
        parse_mode='Markdown',
    )
    return WAIT_RECEIPT


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
            refunded = refund_order_wallet(oid)
            update_order_status(oid, 'canceled')
            msg = "❌ پرداخت لغو شد."
            if refunded:
                msg += f"\n💰 {refunded:,} تومان به کیف پول برگشت."
            await query.edit_message_text(msg)
            await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
            return ConversationHandler.END
    await query.edit_message_text("❌ پرداخت لغو شد.")
    await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    return ConversationHandler.END


async def admin_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    order_id = int(query.data.split('_')[-1])
    mark_receipt_reviewed(order_id=order_id, status='approved')
    order = get_order(order_id)
    if not order:
        await _edit_review_message(query, "❌ سفارش پیدا نشد.")
        return
    if order[3] in ('delivered', 'completed'):
        await _edit_review_message(query, f"سفارش #{order_id} قبلاً تحویل شده.")
        return

    success, status = fulfill_order(order_id)
    tg_id = order[6]
    if success and status == 'sense_manual':
        await _notify_sense_sale(ctx.bot, order_id)
        await _edit_review_message(
            query,
            f"✅ سفارش #{order_id} تایید شد (پک سنس).\nآیدی کاربر برایت ارسال شد."
        )
        await _notify_user(
            ctx.bot,
            tg_id,
            f"✅ سفارش #{order_id} تایید شد.\nپک سنس به‌زودی در پیوی برات ارسال می‌شود.",
        )
    elif success and status in ('delivered', 'paid'):
        await _edit_review_message(
            query, f"✅ سفارش #{order_id} تایید و پردازش شد ({status})."
        )
        await _notify_user(
            ctx.bot,
            tg_id,
            f"✅ سفارش #{order_id} تایید شد.\n"
            + ("💎 جم به اکانتت واریز شد." if status == 'delivered' else "سفارش ثبت شد."),
        )
    else:
        await _alert_fulfill_issue(ctx.bot, order_id, status, 'card_transfer')
        await _edit_review_message(
            query,
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
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    order_id = int(query.data.split('_')[-1])
    mark_receipt_reviewed(order_id=order_id, status='rejected')
    order = get_order(order_id)
    refunded = 0
    if order and order[3] == 'pending':
        refunded = refund_order_wallet(order_id)
        update_order_status(order_id, 'canceled')
    await _edit_review_message(query, f"❌ سفارش #{order_id} رد شد.")
    if order and order[6]:
        extra = f"\n💰 {refunded:,} تومان به کیف پول برگشت." if refunded else ""
        await _notify_user(
            ctx.bot,
            order[6],
            f"❌ سفارش #{order_id} رد شد.{extra}\n"
            f"اگر مبلغی واریز کردی با پشتیبانی در ارتباط باش.",
        )


def payment_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(paid_claim_start, pattern=r'^paid_done_\d+$')],
        states={
            WAIT_RECEIPT: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.IMAGE),
                    receive_receipt,
                ),
                MessageHandler(
                    (filters.TEXT & ~filters.COMMAND) | filters.Document.ALL,
                    receipt_photo_required,
                ),
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
    payable = get_order_payable(order_id) or order[2]
    ok, ref_id = verify_payment(payable, auth)
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
