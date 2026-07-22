from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu, wallet_keyboard
from db import get_or_create_user, get_user_orders, get_wallet_balance


async def my_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            tg_user.id, tg_user.first_name or '', tg_user.last_name or '', tg_user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip() or 'کاربر'
    username = f"@{tg_user.username}" if tg_user.username else "—"
    balance = get_wallet_balance(db_id)

    text = (
        f"✦ *حساب من*\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"نام: {name}\n"
        f"آیدی: *{username}*\n"
        f"شناسه عددی: `{tg_user.id}`\n"
        f"کیف پول: *{balance:,}* تومان"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=wallet_keyboard()
        )
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())


async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = get_or_create_user(
            tg_user.id, tg_user.first_name or '', tg_user.last_name or '', tg_user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    orders = get_user_orders(db_id)
    if not orders:
        text = "📦 *سفارش‌های من*\n\nهنوز سفارشی ثبت نکردی!"
    else:
        STATUS_FA = {
            'pending': '⏳ در انتظار پرداخت/تایید',
            'paid': '✅ پرداخت شده',
            'delivered': '🎉 تحویل شده',
            'processing': '🔄 در حال پردازش',
            'completed': '🎉 تکمیل شده',
            'canceled': '❌ لغو شده',
            'cancelled': '❌ لغو شده',
            'failed': '❌ ناموفق',
        }
        lines = ["📦 *سفارش‌های من*", "━━━━━━━━━━━━━━━"]
        for o in orders[:10]:
            status_fa = STATUS_FA.get(o[2], o[2])
            lines.append(f"🔹 سفارش #{o[0]} • {o[1]:,} ت • {status_fa}")
        text = "\n".join(lines)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=None)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
