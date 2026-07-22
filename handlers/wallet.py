"""کیف پول کاربر — نمایش موجودی و شارژ با زرین‌پال."""
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

from keyboards import wallet_keyboard, wallet_charge_pay_keyboard
from db import (
    get_or_create_user, get_wallet_balance, create_wallet_charge_tx,
    complete_wallet_charge_by_authority, get_conn,
)
from payments import request_payment, verify_payment

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

CALLBACK_BASE = (os.getenv('PAYMENT_CALLBACK_BASE') or '').strip().rstrip('/')
if not CALLBACK_BASE:
    _domain = (os.getenv('BOT_DOMAIN') or 'botatomic.atomicshop.ir').strip()
    CALLBACK_BASE = _domain if _domain.startswith('http') else f'https://{_domain}'
CALLBACK_BASE = CALLBACK_BASE.rstrip('/')
WAIT_CUSTOM = 0

VPN_WARNING = (
    "⚠️ تلگرام فیلتر است؛ اول لینک را *کپی* کن، بعد VPN را خاموش کن و در مرورگر باز کن.\n"
)


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
        "برای شارژ، یکی از مبالغ زیر را انتخاب کن یا مبلغ دلخواه بفرست."
    )
    kb = wallet_keyboard()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)


async def _begin_charge(ctx, user, amount, reply):
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    if not CALLBACK_BASE:
        await reply("❌ آدرس callback درگاه تنظیم نشده (`PAYMENT_CALLBACK_BASE`).")
        return

    callback_url = f"{CALLBACK_BASE}/payment/wallet-callback"
    authority, pay_url, err = request_payment(
        amount,
        f"شارژ کیف پول Atomic Bot — {amount:,} تومان",
        callback_url,
    )
    if not authority or not pay_url:
        await reply(f"❌ ساخت لینک زرین‌پال ممکن نشد.\nعلت: {err or 'نامشخص'}")
        return

    create_wallet_charge_tx(db_id, amount, authority)
    ctx.user_data['wallet_charge'] = {
        'authority': authority,
        'amount': amount,
        'db_id': db_id,
    }
    tx_key = authority[-12:]
    ctx.application.bot_data.setdefault('wallet_auth', {})[tx_key] = authority

    await reply(
        f"✦ *شارژ کیف پول*\n"
        f"مبلغ: *{amount:,}* تومان\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"*راهنما*\n"
        f"۱ · لینک را لمس کن و *کپی* کن\n"
        f"۲ · *VPN را خاموش* کن\n"
        f"۳ · در مرورگر باز کن و پرداخت کن\n"
        f"۴ · برگرد و «پرداخت کردم» را بزن\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"🔗 *لینک پرداخت*\n"
        f"`{pay_url}`",
        parse_mode='Markdown',
        reply_markup=wallet_charge_pay_keyboard(tx_key, pay_url),
    )


async def wallet_charge_preset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = int(query.data.split('_')[1])

    async def reply(text, **kwargs):
        await query.edit_message_text(text, **kwargs)

    await _begin_charge(ctx, update.effective_user, amount, reply)
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
    if not raw.isdigit() or int(raw) < 10000:
        await update.message.reply_text("⚠️ مبلغ نامعتبر است. حداقل ۱۰٬۰۰۰ تومان.")
        return WAIT_CUSTOM
    amount = int(raw)

    async def reply(text, **kwargs):
        await update.message.reply_text(text, **kwargs)

    await _begin_charge(ctx, update.effective_user, amount, reply)
    return ConversationHandler.END


async def wallet_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("در حال بررسی…")
    tx_key = query.data.replace('wchk_', '')
    authority = (ctx.application.bot_data.get('wallet_auth') or {}).get(tx_key)
    charge = ctx.user_data.get('wallet_charge') or {}
    if not authority:
        authority = charge.get('authority')
    if not authority:
        await query.edit_message_text("❌ اطلاعات شارژ پیدا نشد. دوباره از کیف پول شروع کن.")
        return

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Amount", "IsPaid" FROM "WalletTransactions" WHERE "Authority"=%s',
            (authority,),
        )
        row = cur.fetchone()
    if not row:
        await query.edit_message_text("❌ تراکنش پیدا نشد.")
        return
    amount, is_paid = row
    if is_paid:
        bal = get_wallet_balance(ctx.user_data.get('db_id') or 0)
        await query.edit_message_text(
            f"✅ این شارژ قبلاً اعمال شده.\nموجودی: *{bal:,} تومان*",
            parse_mode='Markdown',
            reply_markup=wallet_keyboard(),
        )
        return

    ok, ref = verify_payment(amount, authority)
    if not ok:
        await query.edit_message_text(
            "⏳ پرداخت هنوز تایید نشده. چند لحظه بعد دوباره بزن.\n"
            f"{VPN_WARNING}",
            parse_mode='Markdown',
            reply_markup=wallet_charge_pay_keyboard(
                tx_key, f"https://payment.zarinpal.com/pg/StartPay/{authority}"
            ),
        )
        return

    done, _user_id, amt, new_bal = complete_wallet_charge_by_authority(authority)
    if done:
        ctx.user_data.pop('wallet_charge', None)
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


def wallet_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(wallet_charge_custom_start, pattern='^wchg_custom$')],
        states={
            WAIT_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_charge_custom_amount),
            ],
        },
        fallbacks=[CallbackQueryHandler(wallet_menu, pattern='^wallet$')],
        per_message=False,
        allow_reentry=True,
    )
