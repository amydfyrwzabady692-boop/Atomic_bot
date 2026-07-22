"""احراز هویت برای بسته‌های ۱۱۸۸ و ۲۴۲۰ جم — فقط روش درگاه زرین‌پال."""
import random
import string

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, CommandHandler, filters,
)

from admin_notify import notify_admin, is_admin, admin_id
from keyboards import main_menu, pay_method_keyboard
from db import (
    get_or_create_user, get_kyc_status, is_kyc_approved,
    set_kyc_status, set_kyc_code, get_kyc_code, get_order, get_wallet_balance,
)

WAIT_NAT, WAIT_BANK = range(2)

KYC_PHRASE = "خرید از Atomic Shop"


def _new_code():
    return ''.join(random.choices(string.digits, k=6))


def kyc_instruction_text(code, order_id=None):
    head = "🔐 *احراز هویت الزامی*\n━━━━━━━━━━━━━━━\n"
    if order_id:
        head += f"سفارش #{order_id} — بسته با مبلغ بالا\n\n"
    return (
        head
        + "برای پرداخت با *درگاه زرین‌پال* باید یک‌بار احراز شوی.\n"
        + "(روش *کارت‌به‌کارت* نیاز به احراز ندارد.)\n\n"
        + "📌 این متن را *دقیق* روی کاغذ بنویس:\n"
        + f"`{KYC_PHRASE} — کد {code}`\n\n"
        + "📷 عکس ۱: کارت ملی + همان دست‌نوشته در *یک قاب*\n"
        + "💳 عکس ۲: کارت بانکی که با آن پرداخت می‌کنی\n\n"
        + "بعد از تایید ادمین، دیگر لازم نیست دوباره احراز کنی."
    )


def kyc_begin_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📤 شروع ارسال مدارک', callback_data=f'kyc_begin_{order_id}')],
        [InlineKeyboardButton('🔙 روش‌های پرداخت', callback_data=f'pay_back_{order_id}')],
        [InlineKeyboardButton('❌ انصراف', callback_data=f'cancel_order_{order_id}')],
    ])


def kyc_admin_keyboard(tg_id, order_id=0):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('✅ تایید احراز', callback_data=f'kyc_ok_{tg_id}_{order_id}'),
            InlineKeyboardButton('❌ رد احراز', callback_data=f'kyc_no_{tg_id}_{order_id}'),
        ],
    ])


def kyc_cancel_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('❌ انصراف از احراز', callback_data=f'kyc_cancel_{order_id}')],
    ])


async def prompt_kyc_for_order(query, user, order_id):
    """نمایش راهنمای احراز (قبل از ساخت لینک زرین‌پال)."""
    status = get_kyc_status(telegram_id=user.id)
    if status == 'pending':
        await query.edit_message_text(
            "⏳ مدارک احرازت در صف بررسی ادمین است.\n"
            "بعد از تایید، دوباره «دریافت لینک زرین‌پال» را بزن.\n\n"
            "اگر می‌خواهی مدارک را دوباره بفرستی:",
            reply_markup=kyc_begin_keyboard(order_id),
        )
        return

    code = _new_code()
    set_kyc_code(user.id, code)
    await query.edit_message_text(
        kyc_instruction_text(code, order_id),
        parse_mode='Markdown',
        reply_markup=kyc_begin_keyboard(order_id),
    )


async def kyc_begin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.replace('kyc_begin_', ''))
    user = update.effective_user

    if is_kyc_approved(user.id):
        await query.edit_message_text(
            "✅ قبلاً احراز شده‌ای. دوباره «دریافت لینک زرین‌پال» را بزن.",
            reply_markup=pay_method_keyboard(order_id, can_wallet=False),
        )
        return ConversationHandler.END

    code = get_kyc_code(user.id) or _new_code()
    set_kyc_code(user.id, code)
    ctx.user_data['kyc'] = {
        'order_id': order_id,
        'code': code,
        'nat_file_id': None,
        'bank_file_id': None,
    }

    await query.edit_message_text(
        f"📷 *عکس ۱ را بفرست*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"کارت ملی + دست‌نوشته زیر در *یک عکس*:\n"
        f"`{KYC_PHRASE} — کد {code}`\n\n"
        f"فقط *عکس* بفرست.",
        parse_mode='Markdown',
        reply_markup=kyc_cancel_keyboard(order_id),
    )
    return WAIT_NAT


async def kyc_recv_national(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kyc = ctx.user_data.get('kyc') or {}
    if not update.message.photo:
        await update.message.reply_text(
            "لطفاً *عکس* کارت ملی + دست‌نوشته را بفرست.", parse_mode='Markdown'
        )
        return WAIT_NAT
    kyc['nat_file_id'] = update.message.photo[-1].file_id
    ctx.user_data['kyc'] = kyc
    code = kyc.get('code') or '—'
    await update.message.reply_text(
        f"✅ عکس ۱ دریافت شد.\n\n"
        f"💳 حالا *عکس ۲* را بفرست:\n"
        f"کارت بانکی که با آن می‌خواهی در زرین‌پال پرداخت کنی.\n"
        f"(شماره کارت خوانا باشد)\n\n"
        f"کد احراز تو: `{code}`",
        parse_mode='Markdown',
        reply_markup=kyc_cancel_keyboard(kyc.get('order_id') or 0),
    )
    return WAIT_BANK


async def kyc_recv_bank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kyc = ctx.user_data.get('kyc') or {}
    if not update.message.photo:
        await update.message.reply_text(
            "لطفاً *عکس* کارت بانکی را بفرست.", parse_mode='Markdown'
        )
        return WAIT_BANK

    kyc['bank_file_id'] = update.message.photo[-1].file_id
    user = update.effective_user
    order_id = kyc.get('order_id') or 0
    code = kyc.get('code') or ''

    set_kyc_status(user.id, 'pending', code=code)
    uname = f"@{user.username}" if user.username else "—"
    caption = (
        f"🔐 *مدارک احراز هویت*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"کاربر: {user.full_name} ({uname})\n"
        f"تلگرام: `{user.id}`\n"
        f"سفارش: #{order_id}\n"
        f"کد دست‌نوشته: `{code}`\n"
        f"باید نوشته باشد:\n`{KYC_PHRASE} — کد {code}`"
    )
    await notify_admin(ctx.bot, caption, reply_markup=kyc_admin_keyboard(user.id, order_id))

    aid = admin_id()
    if aid:
        try:
            await ctx.bot.send_photo(
                chat_id=aid,
                photo=kyc['nat_file_id'],
                caption=f"📷 کارت ملی + دست‌نوشته — `{user.id}`",
                parse_mode='Markdown',
            )
            await ctx.bot.send_photo(
                chat_id=aid,
                photo=kyc['bank_file_id'],
                caption=f"💳 کارت بانکی — `{user.id}`",
                parse_mode='Markdown',
                reply_markup=kyc_admin_keyboard(user.id, order_id),
            )
        except Exception as e:
            print(f'[KYC] forward photos failed: {e}')

    await update.message.reply_text(
        "✅ مدارک ارسال شد و در صف بررسی ادمین است.\n"
        "بعد از تایید، دوباره «دریافت لینک زرین‌پال» را بزن.\n"
        "اگر عجله داری، می‌توانی از *کارت‌به‌کارت* (بدون احراز) استفاده کنی.",
        reply_markup=main_menu(),
        parse_mode='Markdown',
    )
    ctx.user_data.pop('kyc', None)
    return ConversationHandler.END


async def kyc_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("انصراف")
    parts = query.data.split('_')
    order_id = int(parts[-1]) if parts[-1].isdigit() else 0
    ctx.user_data.pop('kyc', None)
    await query.edit_message_text("احراز لغو شد.")
    if order_id:
        await query.message.reply_text(
            "روش پرداخت را انتخاب کن (کارت‌به‌کارت بدون احراز):",
            reply_markup=pay_method_keyboard(order_id, can_wallet=False),
        )
    else:
        await query.message.reply_text("منوی اصلی:", reply_markup=main_menu())
    return ConversationHandler.END


async def kyc_cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('kyc', None)
    await update.message.reply_text("احراز لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END


async def admin_kyc_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    rest = query.data.replace('kyc_ok_', '')
    parts = rest.split('_')
    tg = parts[0]
    order_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    set_kyc_status(tg, 'approved')
    try:
        await query.edit_message_caption(
            caption=f"✅ احراز کاربر `{tg}` تایید شد.", parse_mode='Markdown'
        )
    except Exception:
        await query.edit_message_text(
            f"✅ احراز کاربر `{tg}` تایید شد.", parse_mode='Markdown'
        )
    try:
        text = (
            "✅ *احراز هویت تایید شد*\n"
            "حالا می‌توانی از *درگاه زرین‌پال* برای بسته‌های بالا استفاده کنی."
        )
        kb = main_menu()
        if order_id:
            order = get_order(order_id)
            if order and order[3] == 'pending':
                text += f"\n\nسفارش #{order_id} باز است — روش پرداخت را انتخاب کن:"
                bal = 0
                try:
                    from db import get_user_profile, get_order_payable
                    p = get_user_profile(telegram_id=tg)
                    if p:
                        bal = get_wallet_balance(p[0])
                    rem = get_order_payable(order_id)
                except Exception:
                    rem = order[2]
                kb = pay_method_keyboard(
                    order_id,
                    can_wallet=bal > 0,
                    wallet_balance=bal,
                    remaining=rem,
                )
        await ctx.bot.send_message(
            chat_id=int(tg), text=text, parse_mode='Markdown', reply_markup=kb
        )
    except Exception:
        pass


async def admin_kyc_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("دسترسی نداری", show_alert=True)
        return
    rest = query.data.replace('kyc_no_', '')
    parts = rest.split('_')
    tg = parts[0]
    order_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    set_kyc_status(tg, 'rejected', reject_reason='مدارک ناقص/نامعتبر')
    try:
        await query.edit_message_caption(
            caption=f"❌ احراز کاربر `{tg}` رد شد.", parse_mode='Markdown'
        )
    except Exception:
        await query.edit_message_text(
            f"❌ احراز کاربر `{tg}` رد شد.", parse_mode='Markdown'
        )
    try:
        kb = pay_method_keyboard(order_id, can_wallet=False) if order_id else main_menu()
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=(
                "❌ احراز هویت رد شد.\n"
                "مدارک را واضح‌تر بفرست (کارت ملی + دست‌نوشته خوانا + کارت بانکی).\n"
                "دوباره «دریافت لینک زرین‌پال» را بزن.\n"
                "یا از *کارت‌به‌کارت* استفاده کن (بدون احراز)."
            ),
            parse_mode='Markdown',
            reply_markup=kb,
        )
    except Exception:
        pass


async def pay_back_methods(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.replace('pay_back_', ''))
    order = get_order(order_id)
    if not order or order[3] != 'pending':
        await query.edit_message_text("سفارش قابل پرداخت نیست.")
        return
    user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            user.id, user.first_name or '', user.last_name or '', user.username or ''
        )
        ctx.user_data['db_id'] = db_id
    from db import get_order_payable
    bal = get_wallet_balance(db_id)
    rem = get_order_payable(order_id)
    note = f"\nکسر کیف پول: {(order[2] - rem):,} ت" if rem < order[2] else ""
    await query.edit_message_text(
        f"💳 روش پرداخت — سفارش #{order_id}\n"
        f"مبلغ کل: *{order[2]:,}* ت{note}\n"
        f"قابل پرداخت: *{rem:,}* تومان\n"
        f"موجودی کیف پول: {bal:,} ت",
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(
            order_id,
            can_wallet=bal > 0 and rem > 0,
            wallet_balance=bal,
            remaining=rem,
        ),
    )


def kyc_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(kyc_begin, pattern=r'^kyc_begin_\d+$'),
        ],
        states={
            WAIT_NAT: [
                MessageHandler(filters.PHOTO, kyc_recv_national),
                CallbackQueryHandler(kyc_cancel, pattern=r'^kyc_cancel_\d+$'),
            ],
            WAIT_BANK: [
                MessageHandler(filters.PHOTO, kyc_recv_bank),
                CallbackQueryHandler(kyc_cancel, pattern=r'^kyc_cancel_\d+$'),
            ],
        },
        fallbacks=[
            CommandHandler('cancel', kyc_cancel_cmd),
            CallbackQueryHandler(kyc_cancel, pattern=r'^kyc_cancel_\d+$'),
        ],
        allow_reentry=True,
    )
