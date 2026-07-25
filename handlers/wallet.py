"""کیف پول — شارژ با زرین‌پال و کارت‌به‌کارت + بررسی با مهلت."""
import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

from keyboards import (
    wallet_keyboard, wallet_charge_pay_keyboard, wallet_charge_method_keyboard,
    wallet_card_pay_keyboard, admin_wallet_card_keyboard,
    admin_wallet_card_confirm_keyboard, main_menu,
)
from db import (
    get_or_create_user, get_wallet_balance, create_wallet_charge_tx,
    complete_wallet_charge_by_authority, create_wallet_card_charge,
    get_conn, get_wallet_tx, approve_wallet_card_charge,
    reject_wallet_card_charge,
    get_setting, get_bool_setting,
    save_payment_receipt, log_payment_attempt, log_admin_action,
)
from payments import request_payment, verify_payment
from payment_safety import MIN_WALLET_CHARGE, checked_amount
from admin_notify import is_admin, notify_admin

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

CALLBACK_BASE = (os.getenv('PAYMENT_CALLBACK_BASE') or '').strip().rstrip('/')
if not CALLBACK_BASE:
    _domain = (os.getenv('BOT_DOMAIN') or 'botatomic.atomicshop.ir').strip()
    CALLBACK_BASE = _domain if _domain.startswith('http') else f'https://{_domain}'
CALLBACK_BASE = CALLBACK_BASE.rstrip('/')

CARD_NUMBER = (
    os.getenv('CARD_TRANSFER_NUMBER') or os.getenv('CARD_NUMBER') or ''
).strip()
CARD_HOLDER = (
    os.getenv('CARD_TRANSFER_HOLDER') or os.getenv('CARD_HOLDER') or ''
).strip()
CARD_BANK = (
    os.getenv('CARD_TRANSFER_BANK') or os.getenv('CARD_BANK') or ''
).strip()

WAIT_CUSTOM = 0
WAIT_WCARD_RECEIPT = 1
ZP_TTL_SEC = 15 * 60
ZP_MAX_CHECKS = 10

VPN_WARNING = (
    "⚠️ تلگرام فیلتر است؛ اول لینک را *کپی* کن، بعد VPN را خاموش کن و در مرورگر باز کن.\n"
)


def _card_pretty(num: str) -> str:
    digits = ''.join(c for c in (num or '') if c.isdigit())
    if len(digits) == 16:
        return ' '.join(digits[i:i + 4] for i in range(0, 16, 4))
    return num or '—'


async def wallet_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    balance = get_wallet_balance(db_id)
    text = (
        "💰 *کیف پول Atomic*\n"
        "━━━━━━━━━━━━━━━\n"
        f"موجودی فعلی: *{balance:,} تومان*\n\n"
        "مبلغ شارژ را انتخاب کن؛ بعد روش پرداخت (درگاه یا کارت‌به‌کارت) را می‌گیری."
    )
    kb = wallet_keyboard()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)


async def _show_charge_methods(reply, amount):
    try:
        amount = checked_amount(
            amount, minimum=MIN_WALLET_CHARGE, label='مبلغ شارژ کیف پول'
        )
    except ValueError as e:
        await reply(f"❌ {e}", reply_markup=wallet_keyboard())
        return False
    await reply(
        f"✦ *شارژ کیف پول*\n"
        f"مبلغ: *{amount:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"روش پرداخت را انتخاب کن:",
        parse_mode='Markdown',
        reply_markup=wallet_charge_method_keyboard(amount),
    )
    return True


async def wallet_charge_preset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        amount = checked_amount(
            query.data.split('_')[1],
            minimum=MIN_WALLET_CHARGE,
            label='مبلغ شارژ کیف پول',
        )
    except ValueError as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=wallet_keyboard())
        return ConversationHandler.END

    async def reply(text, **kwargs):
        await query.edit_message_text(text, **kwargs)

    await _show_charge_methods(reply, amount)
    return ConversationHandler.END


async def wallet_charge_custom_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ مبلغ شارژ را به *تومان* بفرست (حداقل ۱۰٬۰۰۰):\n"
        "مثال: `150000`",
        parse_mode='Markdown',
    )
    return WAIT_CUSTOM


async def wallet_charge_custom_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or '').strip()
    raw = raw.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    raw = raw.replace(',', '').replace('،', '').replace(' ', '')
    try:
        amount = checked_amount(
            raw, minimum=MIN_WALLET_CHARGE, label='مبلغ شارژ کیف پول'
        )
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return WAIT_CUSTOM

    async def reply(text, **kwargs):
        await update.message.reply_text(text, **kwargs)

    await _show_charge_methods(reply, amount)
    return ConversationHandler.END


async def wallet_pay_zarinpal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not get_bool_setting('payments_enabled', True):
        await query.edit_message_text("⛔ پرداخت جدید موقتاً توسط مدیریت متوقف شده است.")
        return
    try:
        amount = checked_amount(
            query.data.replace('wpay_zp_', ''),
            minimum=MIN_WALLET_CHARGE,
            label='مبلغ شارژ کیف پول',
        )
    except ValueError as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=wallet_keyboard())
        return
    user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    if not get_bool_setting('zarinpal_enabled', True):
        await query.edit_message_text("❌ درگاه زرین‌پال موقتاً غیرفعال است.")
        return
    callback_base = get_setting('payment_callback_base', CALLBACK_BASE).rstrip('/')
    if not callback_base:
        await query.edit_message_text("❌ آدرس callback درگاه تنظیم نشده (`PAYMENT_CALLBACK_BASE`).")
        return

    callback_url = f"{callback_base}/payment/wallet-callback"
    authority, pay_url, err = await asyncio.to_thread(
        request_payment,
        amount,
        f"شارژ کیف پول Atomic Bot — {amount:,} تومان",
        callback_url,
    )
    if not authority or not pay_url:
        log_payment_attempt(
            provider='zarinpal', event='wallet_request', status='failed',
            amount=amount, telegram_id=user.id, message=err,
        )
        await query.edit_message_text(
            f"❌ ساخت لینک زرین‌پال ممکن نشد.\nعلت: {err or 'نامشخص'}",
            reply_markup=wallet_charge_method_keyboard(amount),
        )
        return

    tx_id = create_wallet_charge_tx(db_id, amount, authority)
    # شناسه دیتابیس پایدار است و بعد از restart نیز قابل بازیابی می‌ماند.
    tx_key = str(tx_id)
    ctx.user_data['wallet_charge'] = {
        'authority': authority,
        'amount': amount,
        'db_id': db_id,
        'started': time.time(),
        'checks': 0,
    }
    ctx.application.bot_data.setdefault('wallet_auth', {})[tx_key] = authority
    ctx.application.bot_data.setdefault('wallet_zp_meta', {})[tx_key] = {
        'started': time.time(),
        'checks': 0,
        'amount': amount,
    }
    log_payment_attempt(
        provider='zarinpal', event='wallet_request', status='pending',
        amount=amount, wallet_tx_id=tx_id, telegram_id=user.id,
        authority=authority,
    )

    await query.edit_message_text(
        f"✦ *شارژ با زرین‌پال*\n"
        f"مبلغ: *{amount:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"*راهنما*\n"
        f"۱ · لینک را لمس کن و *کپی* کن\n"
        f"۲ · *VPN را خاموش* کن\n"
        f"۳ · در مرورگر باز کن و پرداخت کن\n"
        f"۴ · برگرد و «پرداخت کردم» را بزن\n"
        f"⏱ مهلت حدود ۱۵ دقیقه\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"🔗 *لینک پرداخت*\n"
        f"`{pay_url}`",
        parse_mode='Markdown',
        reply_markup=wallet_charge_pay_keyboard(tx_key, pay_url),
    )


async def wallet_pay_card(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not get_bool_setting('payments_enabled', True):
        await query.edit_message_text("⛔ پرداخت جدید موقتاً توسط مدیریت متوقف شده است.")
        return
    try:
        amount = checked_amount(
            query.data.replace('wpay_card_', ''),
            minimum=MIN_WALLET_CHARGE,
            label='مبلغ شارژ کیف پول',
        )
    except ValueError as e:
        await query.edit_message_text(f"❌ {e}", reply_markup=wallet_keyboard())
        return
    user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    if not get_bool_setting('card_transfer_enabled', True):
        await query.edit_message_text("❌ کارت‌به‌کارت موقتاً غیرفعال است.")
        return
    card_number = get_setting('card_number', CARD_NUMBER)
    card_holder = get_setting('card_holder', CARD_HOLDER)
    card_bank = get_setting('card_bank', CARD_BANK)
    if not card_number:
        await query.edit_message_text("❌ شماره کارت تنظیم نشده. از درگاه استفاده کن.")
        return

    tx_id, authority = create_wallet_card_charge(db_id, amount)
    tx_key = str(tx_id)
    ctx.user_data['wallet_card'] = {
        'tx_id': tx_id,
        'authority': authority,
        'amount': amount,
        'db_id': db_id,
    }
    ctx.application.bot_data.setdefault('wallet_card_tx', {})[tx_key] = {
        'tx_id': tx_id,
        'authority': authority,
        'amount': amount,
        'db_id': db_id,
        'tg_id': user.id,
    }

    bank = f"بانک: *{card_bank}*\n" if card_bank else ""
    await query.edit_message_text(
        f"✦ *شارژ کارت‌به‌کارت*\n"
        f"مبلغ: *{amount:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"شماره کارت\n"
        f"`{_card_pretty(card_number)}`\n"
        f"به نام *{card_holder or '—'}*\n"
        f"{bank}"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"۱ · مبلغ را *دقیق* واریز کن\n"
        f"۲ · «پرداخت کردم» را بزن و عکس رسید بفرست\n"
        f"۳ · بعد از تایید ادمین، موجودی شارژ می‌شود\n"
        f"\n_روی شماره کارت بزن تا کپی شود_",
        parse_mode='Markdown',
        reply_markup=wallet_card_pay_keyboard(tx_key),
    )


async def wallet_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("در حال بررسی…")
    tx_key = query.data.replace('wchk_', '')
    if not tx_key.isdigit():
        await query.edit_message_text("❌ شناسه تراکنش شارژ نامعتبر است.")
        return
    tx_id_from_button = int(tx_key)
    charge = ctx.user_data.get('wallet_charge') or {}

    meta = (ctx.application.bot_data.get('wallet_zp_meta') or {}).get(tx_key) or {}
    if not meta and charge:
        meta = {
            'started': charge.get('started') or time.time(),
            'checks': charge.get('checks') or 0,
            'amount': charge.get('amount'),
        }
    started = float(meta.get('started') or time.time())
    checks = int(meta.get('checks') or 0) + 1
    meta.update({'started': started, 'checks': checks})
    ctx.application.bot_data.setdefault('wallet_zp_meta', {})[tx_key] = meta
    elapsed = time.time() - started
    left = max(0, int(ZP_TTL_SEC - elapsed))

    user = update.effective_user
    db_id, _ = get_or_create_user(
        user.id, user.first_name or '', user.last_name or '', user.username or ''
    )
    ctx.user_data['db_id'] = db_id
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT t."Id",t."Amount",t."IsPaid",w."UserId",u."TelegramId",'
            't."Authority" '
            'FROM "WalletTransactions" t '
            'JOIN "Wallets" w ON w."Id"=t."WalletId" '
            'LEFT JOIN "Users" u ON u."Id"=w."UserId" '
            'WHERE t."Id"=%s AND t."Kind"=\'charge\' '
            'AND t."Authority" NOT LIKE \'wcard_%%\'',
            (tx_id_from_button,),
        )
        row = cur.fetchone()
    if not row:
        await query.edit_message_text("❌ تراکنش پیدا نشد.")
        return
    tx_id, amount, is_paid, owner_db_id, owner_tg_id, authority = row
    if int(owner_db_id) != int(db_id) or str(owner_tg_id or '') != str(user.id):
        await query.edit_message_text("❌ این تراکنش شارژ متعلق به شما نیست.")
        return
    if is_paid:
        bal = get_wallet_balance(ctx.user_data.get('db_id') or 0)
        await query.edit_message_text(
            f"✅ این شارژ قبلاً اعمال شده.\nموجودی: *{bal:,} تومان*",
            parse_mode='Markdown',
            reply_markup=wallet_keyboard(),
        )
        return

    ok, ref = await asyncio.to_thread(verify_payment, amount, authority)
    if not ok:
        expired = elapsed >= ZP_TTL_SEC or checks >= ZP_MAX_CHECKS
        log_payment_attempt(
            provider='zarinpal', event='wallet_verify',
            status='failed' if expired else 'pending',
            amount=amount, wallet_tx_id=tx_id, telegram_id=user.id,
            authority=authority, message='درگاه شارژ را تأیید نکرد.',
        )
        kb = wallet_charge_pay_keyboard(
            tx_key, f"https://payment.zarinpal.com/pg/StartPay/{authority}"
        )
        if expired:
            await query.edit_message_text(
                f"❌ *پرداخت تایید نشد*\n"
                f"مبلغ: *{amount:,}* تومان\n"
                f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
                f"مهلت درگاه تمام شد یا پرداختی ثبت نشده.\n"
                f"اگر پول کم نشده، دوباره از کیف پول شارژ کن.",
                parse_mode='Markdown',
                reply_markup=wallet_keyboard(),
            )
        else:
            mins = max(1, (left + 59) // 60)
            await query.edit_message_text(
                f"⏳ هنوز پرداخت تایید نشده (بررسی {checks}/{ZP_MAX_CHECKS}).\n"
                f"⏱ حدود *{mins}* دقیقه از مهلت مانده.\n"
                f"اگر لینک را باز نکردی / پرداخت نکردی، صبر کن تا مهلت تمام شود یا روش دیگر را انتخاب کن.\n"
                f"{VPN_WARNING}",
                parse_mode='Markdown',
                reply_markup=kb,
            )
        return

    done, _user_id, amt, new_bal = complete_wallet_charge_by_authority(
        authority, verified_amount=amount, ref_id=ref
    )
    if done:
        log_payment_attempt(
            provider='zarinpal', event='wallet_verified', status='success',
            amount=amt, wallet_tx_id=tx_id, telegram_id=user.id,
            authority=authority, ref_id=ref,
        )
        ctx.user_data.pop('wallet_charge', None)
        (ctx.application.bot_data.get('wallet_zp_meta') or {}).pop(tx_key, None)
        await query.edit_message_text(
            f"✅ کیف پول شارژ شد!\n"
            f"مبلغ: *{amt:,} تومان*\n"
            f"موجودی جدید: *{new_bal:,} تومان*\n"
            f"پیگیری: `{ref}`",
            parse_mode='Markdown',
            reply_markup=wallet_keyboard(),
        )
    else:
        await query.edit_message_text("❌ اعمال شارژ ناموفق بود.")


async def wallet_card_done_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tx_key = query.data.replace('wcard_done_', '')
    if not tx_key.isdigit():
        await query.edit_message_text("❌ شناسه تراکنش نامعتبر است.")
        return ConversationHandler.END
    info = (ctx.application.bot_data.get('wallet_card_tx') or {}).get(tx_key)
    if not info:
        info = ctx.user_data.get('wallet_card')
    row = get_wallet_tx(int(tx_key))
    if row and not info:
        info = {
            'tx_id': row[0], 'authority': row[2], 'amount': row[1],
            'db_id': row[4], 'tg_id': row[5],
        }
    if (
        not info or not row or row[3]
        or int(row[4]) != int(info.get('db_id') or 0)
        or str(row[5] or '') != str(update.effective_user.id)
        or int(row[1]) != int(info.get('amount') or 0)
        or not str(row[2] or '').startswith('wcard_')
    ):
        await query.edit_message_text("❌ اطلاعات شارژ با حساب شما تطابق ندارد.")
        return ConversationHandler.END
    ctx.user_data['wallet_card'] = info
    ctx.user_data['wallet_card_key'] = tx_key
    await query.edit_message_text(
        "🧾 *ارسال رسید شارژ کیف پول*\n"
        "عکس رسید یا کد پیگیری را همین‌جا بفرست.",
        parse_mode='Markdown',
    )
    return WAIT_WCARD_RECEIPT


async def wallet_card_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = ctx.user_data.get('wallet_card')
    if not info:
        await update.message.reply_text("اطلاعات شارژ منقضی شد. از کیف پول دوباره شروع کن.")
        return ConversationHandler.END
    user = update.effective_user
    tx_id = info['tx_id']
    amount = info['amount']
    row = get_wallet_tx(tx_id)
    if (
        not row or row[3] or int(row[1]) != int(amount)
        or str(row[5] or '') != str(user.id)
        or not str(row[2] or '').startswith('wcard_')
    ):
        await update.message.reply_text(
            "❌ تراکنش منقضی، پرداخت‌شده یا متعلق به حساب دیگری است.",
            reply_markup=wallet_keyboard(),
        )
        return ConversationHandler.END
    uname = f"@{user.username}" if user.username else "—"
    caption = (
        f"🆕 رسید شارژ کیف پول\n"
        f"تراکنش #{tx_id}\n"
        f"مبلغ: {amount:,} ت\n"
        f"کاربر: {user.full_name} ({uname})\n"
        f"تلگرام: `{user.id}`"
    )
    file_id = (update.message.photo[-1].file_id if update.message.photo else
               update.message.document.file_id if update.message.document else '')
    try:
        save_payment_receipt(
            wallet_tx_id=tx_id, telegram_id=user.id, file_id=file_id,
            text=update.message.text or update.message.caption or '',
        )
        log_payment_attempt(
            provider='card_transfer', event='wallet_receipt_submitted',
            status='pending', amount=amount, wallet_tx_id=tx_id,
            telegram_id=user.id,
        )
    except ValueError as e:
        ctx.user_data.pop('wallet_card', None)
        await update.message.reply_text(
            f"❌ رسید ثبت نشد: {e}", reply_markup=wallet_keyboard()
        )
        return ConversationHandler.END
    except Exception as e:
        print(f'[WALLET] receipt persistence failed: {e}')
        ctx.user_data.pop('wallet_card', None)
        await update.message.reply_text(
            "❌ ذخیره رسید انجام نشد؛ دوباره تلاش کن.",
            reply_markup=wallet_keyboard(),
        )
        return ConversationHandler.END
    try:
        from admin_notify import admin_ids
        recipients = admin_ids()
        if not recipients:
            await update.message.reply_text("❌ ادمین تنظیم نشده.")
            return ConversationHandler.END
        if update.message.photo:
            for aid in recipients:
                await ctx.bot.send_photo(
                    chat_id=aid, photo=update.message.photo[-1].file_id,
                    caption=caption, parse_mode='Markdown',
                    reply_markup=admin_wallet_card_keyboard(tx_id),
                )
        else:
            text = caption
            if update.message.document:
                for aid in recipients:
                    await ctx.bot.send_document(
                        chat_id=aid, document=update.message.document.file_id,
                        caption=caption, parse_mode='Markdown',
                        reply_markup=admin_wallet_card_keyboard(tx_id),
                    )
            else:
                if update.message.text:
                    text += f"\n\nپیام کاربر:\n{update.message.text}"
                await notify_admin(
                    ctx.bot,
                    text,
                    reply_markup=admin_wallet_card_keyboard(tx_id),
                )
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال به ادمین ناموفق: {e}")
        return ConversationHandler.END

    ctx.user_data.pop('wallet_card', None)
    await update.message.reply_text(
        "✅ رسید ارسال شد.\nبعد از تایید ادمین، موجودی شارژ می‌شود.",
        reply_markup=wallet_keyboard(),
    )
    return ConversationHandler.END


async def wallet_receipt_photo_required(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Keep the flow open until an actual image receipt is received."""
    await update.message.reply_text(
        "⚠️ لطفاً عکس رسید را به‌صورت Photo یا فایل تصویری ارسال کن؛ "
        "متن و فایل غیرتصویری برای بررسی پرداخت پذیرفته نمی‌شود."
    )
    return WAIT_WCARD_RECEIPT


async def admin_wallet_card_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    tx_id = int(query.data.replace('wadmin_ok_', ''))
    review = ctx.user_data.pop('admin_wallet_review', None) or {}
    if (
        review.get('action') != 'ok' or review.get('tx_id') != tx_id
        or time.time() - float(review.get('armed_at') or 0) > 120
    ):
        await query.answer(
            "ابتدا «بررسی برای تأیید» را بزن؛ تأیید نهایی ۲ دقیقه اعتبار دارد.",
            show_alert=True,
        )
        return
    await query.answer()
    row = get_wallet_tx(tx_id)
    if not row:
        await query.edit_message_text("❌ تراکنش پیدا نشد.")
        return
    # Id, Amount, Authority, IsPaid, UserId, TelegramId, Balance
    _id, amount, authority, is_paid, user_id, tg_id, _bal = row
    if is_paid:
        await query.edit_message_text(f"این شارژ قبلاً تایید شده (#{tx_id}).")
        return
    done, _uid, amt, new_bal, approve_status = approve_wallet_card_charge(tx_id)
    if not done:
        await query.edit_message_text(f"❌ اعمال شارژ ناموفق بود: {approve_status}")
        return
    if approve_status == 'approved':
        log_payment_attempt(
            provider='card_transfer', event='wallet_card_approved',
            status='success', amount=amt, wallet_tx_id=tx_id,
            telegram_id=tg_id,
            message=f'approved by {update.effective_user.id}',
        )
        log_admin_action(
            update.effective_user.id, 'wallet_card_approved',
            'wallet_transaction', tx_id, f'amount={amt}',
        )
    try:
        await query.edit_message_caption(
            caption=f"✅ شارژ کیف پول #{tx_id} تایید شد — {amt:,} ت"
        )
    except Exception:
        try:
            await query.edit_message_text(f"✅ شارژ کیف پول #{tx_id} تایید شد — {amt:,} ت")
        except Exception:
            pass
    if tg_id:
        try:
            await ctx.bot.send_message(
                chat_id=int(tg_id),
                text=(
                    f"✅ شارژ کیف پول تایید شد.\n"
                    f"مبلغ: *{amt:,}* ت\n"
                    f"موجودی جدید: *{new_bal:,}* ت"
                ),
                parse_mode='Markdown',
                reply_markup=main_menu(),
            )
        except Exception:
            pass


async def admin_wallet_review_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """مرحله اول تأیید/رد شارژ کارت؛ کلیک بعدی عملیات مالی را انجام می‌دهد."""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    parts = query.data.split('_')
    action = parts[2]
    tx_id = int(parts[3])
    row = get_wallet_tx(tx_id)
    if not row or row[3] or not str(row[2] or '').startswith('wcard_'):
        await query.answer("تراکنش دیگر قابل بررسی نیست.", show_alert=True)
        return
    label = (
        f"شارژ نهایی کیف پول به مبلغ {row[1]:,} تومان؟"
        if action == 'ok'
        else f"رد نهایی رسید شارژ #{tx_id}؟"
    )
    ctx.user_data['admin_wallet_review'] = {
        'action': action, 'tx_id': tx_id, 'armed_at': time.time(),
    }
    await query.answer(label, show_alert=True)
    await query.edit_message_reply_markup(
        reply_markup=admin_wallet_card_confirm_keyboard(tx_id, action)
    )


async def admin_wallet_review_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    tx_id = int(query.data.rsplit('_', 1)[1])
    ctx.user_data.pop('admin_wallet_review', None)
    await query.answer("بازگشت")
    await query.edit_message_reply_markup(
        reply_markup=admin_wallet_card_keyboard(tx_id)
    )


async def admin_wallet_card_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    tx_id = int(query.data.replace('wadmin_no_', ''))
    review = ctx.user_data.pop('admin_wallet_review', None) or {}
    if (
        review.get('action') != 'no' or review.get('tx_id') != tx_id
        or time.time() - float(review.get('armed_at') or 0) > 120
    ):
        await query.answer(
            "ابتدا «بررسی برای رد» را بزن؛ تأیید نهایی ۲ دقیقه اعتبار دارد.",
            show_alert=True,
        )
        return
    await query.answer()
    row = get_wallet_tx(tx_id)
    if not row:
        await query.edit_message_text("❌ تراکنش پیدا نشد.")
        return
    _id, amount, authority, is_paid, user_id, tg_id, _bal = row
    if is_paid:
        await query.edit_message_text("این شارژ قبلاً اعمال شده؛ قابل رد نیست.")
        return
    rejected, reject_error = reject_wallet_card_charge(tx_id)
    if not rejected:
        await query.edit_message_text(f"❌ رد شارژ انجام نشد: {reject_error}")
        return
    log_payment_attempt(
        provider='card_transfer', event='wallet_card_rejected',
        status='rejected', amount=amount, wallet_tx_id=tx_id,
        telegram_id=tg_id,
        message=f'rejected by {update.effective_user.id}',
    )
    log_admin_action(
        update.effective_user.id, 'wallet_card_rejected',
        'wallet_transaction', tx_id, f'amount={amount}',
    )
    try:
        await query.edit_message_caption(caption=f"❌ شارژ کیف پول #{tx_id} رد شد.")
    except Exception:
        try:
            await query.edit_message_text(f"❌ شارژ کیف پول #{tx_id} رد شد.")
        except Exception:
            pass
    if tg_id:
        try:
            await ctx.bot.send_message(
                chat_id=int(tg_id),
                text=(
                    f"❌ شارژ کیف پول ({amount:,} ت) رد شد.\n"
                    f"اگر اشتباه شده، دوباره از کیف پول شارژ کن."
                ),
                reply_markup=wallet_keyboard(),
            )
        except Exception:
            pass


def wallet_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(wallet_charge_custom_start, pattern='^wchg_custom$'),
            CallbackQueryHandler(wallet_card_done_start, pattern=r'^wcard_done_'),
        ],
        states={
            WAIT_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_charge_custom_amount),
            ],
            WAIT_WCARD_RECEIPT: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.IMAGE),
                    wallet_card_receipt,
                ),
                MessageHandler(
                    (filters.TEXT & ~filters.COMMAND) | filters.Document.ALL,
                    wallet_receipt_photo_required,
                ),
            ],
        },
        fallbacks=[CallbackQueryHandler(wallet_menu, pattern='^wallet$')],
        per_message=False,
        allow_reentry=True,
    )
