from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu
from db import get_or_create_user, get_user_orders


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

    text = (
        f"👤 *حساب من*\n\n"
        f"🧑 نام: {name}\n"
        f"🆔 یوزرنیم: {username}\n"
        f"🔢 شناسه تلگرام: `{tg_user.id}`\n"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=None)
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
            'pending': '⏳ در انتظار پرداخت',
            'paid': '✅ پرداخت شده',
            'processing': '🔄 در حال پردازش',
            'completed': '🎉 تکمیل شده',
            'cancelled': '❌ لغو شده',
            'failed': '❌ ناموفق',
        }
        lines = ["📦 *سفارش‌های من:*\n"]
        for o in orders[:10]:  # o = (Id, CreatedAt, TotalAmount, Status)
            status_fa = STATUS_FA.get(o[3], o[3])
            lines.append(f"🔹 سفارش #{o[0]} | {o[2]:,} ت | {status_fa}")
        text = "\n".join(lines)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=None)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
