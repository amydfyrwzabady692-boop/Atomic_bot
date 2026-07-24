"""استودیوی محدود مدیر پریمیوم؛ بدون دسترسی به پول، درگاه یا مدیران."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

import g2bulk
from admin_notify import is_admin, is_premium_admin
from db import (
    get_or_create_user, get_setting, list_payment_attempts, set_setting,
)

WAIT_STUDIO_VALUE = 80
_EDITABLE = {
    'studio_edit_shop': (
        'shop_name',
        'نام جدید فروشگاه را بفرست.',
        80,
    ),
    'studio_edit_welcome': (
        'welcome_text',
        'متن خوش‌آمد جدید را بفرست. می‌توانی از {name} و {welcome} استفاده کنی.',
        3500,
    ),
    'studio_edit_support': (
        'support_text',
        'متن بخش پشتیبانی را بفرست.',
        3500,
    ),
}


def _keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✏️ نام فروشگاه', callback_data='studio_edit_shop')],
        [InlineKeyboardButton('✨ متن خوش‌آمد', callback_data='studio_edit_welcome')],
        [InlineKeyboardButton('🎧 متن پشتیبانی', callback_data='studio_edit_support')],
        [InlineKeyboardButton('💵 موجودی G2Bulk', callback_data='studio_g2')],
        [InlineKeyboardButton('📒 گزارش پرداخت فقط‌خواندنی', callback_data='studio_payments')],
        [InlineKeyboardButton('🔄 بروزرسانی', callback_data='studio_home')],
    ])


async def _guard(update):
    user = update.effective_user
    if not user:
        return False
    get_or_create_user(
        user.id, user.first_name or '', user.last_name or '', user.username or '',
        is_premium=bool(user.is_premium),
    )
    if is_admin(user.id):
        return True
    if bool(user.is_premium) and is_premium_admin(user.id):
        return True
    text = (
        '❌ دسترسی استودیو فعال نیست. مدیر اصلی باید شناسه شما را به‌عنوان '
        '«مدیر پریمیوم» ثبت کند و حساب Telegram Premium فعال باشد.'
    )
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.message:
        await update.message.reply_text(text)
    return False


async def studio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    await update.message.reply_text(
        '⭐ *استودیوی مدیر پریمیوم*\n'
        '━━━━━━━━━━━━━━━\n'
        'این بخش برای ظاهر، متن‌ها و گزارش‌های فقط‌خواندنی است.\n'
        'به مرچنت، کارت، کیف پول کاربران و مدیریت ادمین‌ها دسترسی ندارد.',
        parse_mode='Markdown',
        reply_markup=_keyboard(),
    )


async def studio_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return
    data = query.data
    if data == 'studio_home':
        await query.edit_message_text(
            '⭐ استودیوی مدیر پریمیوم\nیک بخش را انتخاب کن.',
            reply_markup=_keyboard(),
        )
    elif data == 'studio_g2':
        snapshot = g2bulk.get_inventory_snapshot(force=True)
        if not snapshot.get('ok'):
            text = f'❌ موجودی دریافت نشد:\n{snapshot.get("error") or "خطای نامشخص"}'
        else:
            balance = float(snapshot['balance'])
            lines = [f'💵 موجودی G2Bulk: *${balance:,.4f}*', '━━━━━━━━━━━━━━━']
            for amount, cost in sorted(snapshot['prices'].items()):
                count = int(balance // cost) if cost > 0 else 0
                lines.append(f'• {amount} جم · ${cost:.4f} · حدود {count} سفارش')
            text = '\n'.join(lines)
        await query.edit_message_text(
            text, parse_mode='Markdown' if snapshot.get('ok') else None,
            reply_markup=_keyboard(),
        )
    elif data == 'studio_payments':
        rows = list_payment_attempts(limit=20)
        lines = ['📒 *آخرین رویدادهای پرداخت*', '━━━━━━━━━━━━━━━']
        for row in rows:
            (_id, oid, txid, _tg, provider, event, status, amount,
             _authority, _ref, _message, created) = row
            icon = '✅' if status == 'success' else '⏳' if status == 'pending' else '❌'
            target = f'سفارش #{oid}' if oid else f'کیف #{txid}' if txid else '—'
            provider_label = str(provider).replace('_', '-').replace('`', '')
            event_label = str(event).replace('_', '-').replace('`', '')
            lines.append(
                f'{icon} {target} · {provider_label}/{event_label} · {int(amount or 0):,} ت '
                f'· {str(created)[:16]}'
            )
        if not rows:
            lines.append('هنوز رویدادی ثبت نشده است.')
        await query.edit_message_text(
            '\n'.join(lines), parse_mode='Markdown', reply_markup=_keyboard()
        )


async def studio_edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return ConversationHandler.END
    key, prompt, limit = _EDITABLE[query.data]
    ctx.user_data['studio_edit'] = {'key': key, 'limit': limit}
    current = get_setting(key, '')
    preview = current[:300] if current else 'تنظیم نشده'
    await query.edit_message_text(
        f'{prompt}\n\nمقدار فعلی:\n{preview}\n\n/cancel برای انصراف'
    )
    return WAIT_STUDIO_VALUE


async def studio_edit_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    edit = ctx.user_data.get('studio_edit') or {}
    value = (update.message.text or '').strip()
    if not value:
        await update.message.reply_text('❌ مقدار خالی مجاز نیست.')
        return WAIT_STUDIO_VALUE
    if len(value) > int(edit.get('limit') or 0):
        await update.message.reply_text('❌ متن از سقف مجاز طولانی‌تر است.')
        return WAIT_STUDIO_VALUE
    key = edit.get('key')
    if key not in {'shop_name', 'welcome_text', 'support_text'}:
        return ConversationHandler.END
    set_setting(key, value)
    ctx.user_data.pop('studio_edit', None)
    await update.message.reply_text('✅ ذخیره شد.', reply_markup=_keyboard())
    return ConversationHandler.END


async def studio_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('studio_edit', None)
    await update.message.reply_text('انصراف.', reply_markup=_keyboard())
    return ConversationHandler.END


def premium_admin_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                studio_edit_start,
                pattern=r'^studio_edit_(?:shop|welcome|support)$',
            )
        ],
        states={
            WAIT_STUDIO_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, studio_edit_receive)
            ]
        },
        fallbacks=[CommandHandler('cancel', studio_cancel)],
        allow_reentry=True,
    )
