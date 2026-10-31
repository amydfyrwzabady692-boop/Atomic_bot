import asyncio

from telegram import Update
from telegram.ext import ContextTypes
from keyboards import main_menu, wallet_keyboard
import appearance
from db import get_or_create_user, get_user_orders, get_wallet_balance
from text_safety import markdown_safe


async def my_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = await asyncio.to_thread(
            get_or_create_user,
            tg_user.id, tg_user.first_name or '', tg_user.last_name or '', tg_user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    name = markdown_safe(
        f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip() or 'کاربر',
        128,
    )
    username = markdown_safe(f"@{tg_user.username}" if tg_user.username else "—", 64)
    balance = await asyncio.to_thread(get_wallet_balance, db_id)
    from db import identity_state, list_identity_held_orders

    identity = await asyncio.to_thread(identity_state, tg_user.id)
    # احراز هویت فقط وقتی دیده می‌شود که لازم است یا انجام شده؛ قاعده گفته نمی‌شود.
    held = len(await asyncio.to_thread(list_identity_held_orders, tg_user.id, 20))
    identity_fa = {'approved': '✅ تایید شده', 'pending': '⏳ در صف بررسی'}.get(identity)
    if not identity_fa and held:
        identity_fa = ('❌ رد شده؛ دوباره بفرست' if identity == 'rejected'
                       else '⚠️ لازم برای تحویل سفارش منتظر')

    text = (
        f"{appearance.user_label('t.account.hdr', appearance.DEFAULTS['t.account.hdr'])}\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"نام: {name}\n"
        f"آیدی: *{username}*\n"
        f"شناسه عددی: `{tg_user.id}`\n"
        f"کیف پول: *{balance:,}* تومان"
        + (f"\nاحراز هویت: {identity_fa}" if identity_fa else '')
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=wallet_keyboard()
        )
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu())
    if identity in ('none', 'rejected') and held:
        from handlers.identity import fa, start_keyboard

        await update.effective_message.reply_text(
            f'🪪 {fa(held)} سفارش منتظر احراز هویت توست؛ بعد از تایید مدارک خودکار تحویل می‌شود.',
            reply_markup=start_keyboard())


async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    db_id = ctx.user_data.get('db_id')
    if not db_id:
        db_id, _ = await asyncio.to_thread(
            get_or_create_user,
            tg_user.id, tg_user.first_name or '', tg_user.last_name or '', tg_user.username or ''
        )
        ctx.user_data['db_id'] = db_id

    orders = await asyncio.to_thread(get_user_orders, db_id)
    if not orders:
        text = appearance.user_label('t.orders.empty', appearance.DEFAULTS['t.orders.empty'])
    else:
        STATUS_FA = {
            'pending': '⏳ در انتظار پرداخت/تایید',
            'paid': '✅ پرداخت شده',
            'delivered': '🎉 تحویل شده',
            'processing': '🔄 در حال پردازش',
            'kyc_hold': '🪪 منتظر احراز هویت',
            'completed': '🎉 تکمیل شده',
            'canceled': '❌ لغو شده',
            'cancelled': '❌ لغو شده',
            'failed': '❌ ناموفق',
        }
        lines = [appearance.user_label('t.orders.hdr', appearance.DEFAULTS['t.orders.hdr']), "━━━━━━━━━━━━━━━"]
        for o in orders[:10]:
            status_fa = STATUS_FA.get(o[2], o[2])
            lines.append(f"🔹 سفارش #{o[0]} • {o[1]:,} ت • {status_fa}")
        text = "\n".join(lines)
    text += "\n\n🔎 برای پیگیری، شمارهٔ سفارش یا کد سفارش سایت (AS…) را بفرست."

    from handlers.order_track import track_keyboard
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown',
                                                      reply_markup=track_keyboard())
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=track_keyboard())
