"""پنل ادمین «ظاهر» — متن و ایموجی پریمیوم بخش‌های کاربری."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

import appearance
from admin_notify import is_admin
from db import reset_appearance, upsert_appearance
from keyboards import admin_home_keyboard

WAIT_TEXT, WAIT_EMOJI = range(90, 92)


def _guard(update):
    user = update.effective_user
    return bool(user and is_admin(user.id))


async def _deny(update):
    text = '⛔️ فقط مدیر اصلی به بخش ظاهر دسترسی دارد.'
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.message:
        await update.message.reply_text(text)


def _home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            appearance.HUBS['t']['title'], callback_data='ap_h:t'
        )],
        [InlineKeyboardButton(
            appearance.HUBS['b']['title'], callback_data='ap_h:b'
        )],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def _hub_kb(hub_id):
    hub = appearance.HUBS[hub_id]
    rows = [
        [InlineKeyboardButton(cat['title'], callback_data=f'ap_c:{hub_id}:{cat_id}')]
        for cat_id, cat in hub['categories'].items()
    ]
    rows.append([InlineKeyboardButton('🔙 ظاهر', callback_data='ap_home')])
    return InlineKeyboardMarkup(rows)


def _cat_kb(hub_id, category_id):
    rows = []
    for key, title, _long in appearance.category_items(hub_id, category_id):
        mark = ''
        row = appearance.get(key)
        if row.get('text'):
            mark += '✏️'
        if row.get('emoji_id'):
            mark += '⭐'
        label = f'{mark} {title}'.strip()[:64]
        rows.append([InlineKeyboardButton(label, callback_data=f'ap_i:{key}')])
    if not rows:
        rows.append([InlineKeyboardButton(
            'فعلاً موردی در این دسته نیست', callback_data=f'ap_c:{hub_id}:{category_id}'
        )])
    rows.append([InlineKeyboardButton('🔙 دسته‌ها', callback_data=f'ap_h:{hub_id}')])
    return InlineKeyboardMarkup(rows)


def _item_kb(key):
    meta = appearance.find_item_meta(key) or {}
    hub = meta.get('hub') or 't'
    cat = meta.get('category') or 'welcome'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✏️ تغییر متن', callback_data=f'ap_txt:{key}')],
        [InlineKeyboardButton('⭐ ایموجی پریمیوم', callback_data=f'ap_emo:{key}')],
        [InlineKeyboardButton('🗑 حذف ایموجی', callback_data=f'ap_cle:{key}')],
        [InlineKeyboardButton('↩️ بازگشت به پیش‌فرض', callback_data=f'ap_rst:{key}')],
        [InlineKeyboardButton('🔙 دسته', callback_data=f'ap_c:{hub}:{cat}')],
    ])


def _item_text(key):
    meta = appearance.find_item_meta(key) or {}
    title = meta.get('title') or key
    current = appearance.user_label(key, appearance.default_for(key))
    default = appearance.default_for(key)
    emoji = appearance.user_emoji(key)
    preview = current if len(current) <= 700 else current[:700] + '…'
    default_preview = default if len(default) <= 400 else default[:400] + '…'
    emoji_line = '⭐ ست شده' if emoji else 'نیست'
    return (
        f'✨ {title}\n'
        f'{key}\n'
        '━━━━━━━━━━━━━━━\n'
        f'ایموجی پریمیوم: {emoji_line}\n\n'
        f'متن فعلی:\n{preview}\n\n'
        f'پیش‌فرض:\n{default_preview}'
    )


async def appear_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    if not _guard(update):
        await _deny(update)
        return
    text = (
        '✨ *ظاهر ربات*\n'
        '━━━━━━━━━━━━━━━\n'
        'فقط چیزهایی که *کاربر* می‌بیند.\n'
        'قیمت، موجودی، پرداخت و سفارش دست نمی‌خورند.\n\n'
        'دو بخش:\n'
        '📝 متن‌ها و توضیحات صفحات\n'
        '🔘 منوها، دکمه‌ها و محصولات (مثلاً بسته ۱۱۰ جم)'
    )
    markup = _home_kb()
    if query:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=markup)


async def appear_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _guard(update):
        await _deny(update)
        return
    data = query.data or ''
    if data == 'ap_home':
        await appear_home(update, ctx)
        return
    if data.startswith('ap_h:') and data[5:] in appearance.HUBS:
        hub_id = data[5:]
        hub = appearance.HUBS[hub_id]
        await query.edit_message_text(
            f"✨ *{hub['title']}*\n{hub['hint']}\n\nیک دسته را انتخاب کن:",
            parse_mode='Markdown',
            reply_markup=_hub_kb(hub_id),
        )
        return
    if data.startswith('ap_c:'):
        parts = data.split(':')
        if len(parts) != 3 or parts[1] not in appearance.HUBS:
            return
        hub_id, cat_id = parts[1], parts[2]
        cat = appearance.HUBS[hub_id]['categories'].get(cat_id)
        if not cat:
            return
        await query.edit_message_text(
            f"✨ *{cat['title']}*\nگزینه را برای ویرایش متن یا ایموجی انتخاب کن:",
            parse_mode='Markdown',
            reply_markup=_cat_kb(hub_id, cat_id),
        )
        return
    if data.startswith('ap_i:'):
        key = data[5:]
        if not appearance.valid_item_key(key):
            await query.answer('این مورد پیدا نشد.', show_alert=True)
            return
        await query.edit_message_text(
            _item_text(key), reply_markup=_item_kb(key),
        )
        return
    if data.startswith('ap_cle:'):
        key = data[7:]
        if not appearance.valid_item_key(key):
            return
        upsert_appearance(key, clear_emoji=True)
        appearance.invalidate_cache()
        await query.edit_message_text(
            '✅ ایموجی پریمیوم حذف شد.\n\n' + _item_text(key),
            reply_markup=_item_kb(key),
        )
        return
    if data.startswith('ap_rst:'):
        key = data[7:]
        if not appearance.valid_item_key(key):
            return
        reset_appearance(key)
        appearance.invalidate_cache()
        await query.edit_message_text(
            '✅ به پیش‌فرض برگشت.\n\n' + _item_text(key),
            reply_markup=_item_kb(key),
        )


async def appear_text_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _guard(update):
        await _deny(update)
        return ConversationHandler.END
    key = query.data.split(':', 1)[1]
    if not appearance.valid_item_key(key):
        return ConversationHandler.END
    ctx.user_data['appear_edit'] = {'key': key, 'mode': 'text'}
    limit = appearance.max_len_for(key)
    await query.edit_message_text(
        f'✏️ متن جدید را بفرست (حداکثر {limit} نویسه).\n'
        'برای انصراف /cancel\n\n'
        f'مقدار فعلی:\n{appearance.user_label(key, appearance.default_for(key))[:900]}'
    )
    return WAIT_TEXT


async def appear_emoji_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _guard(update):
        await _deny(update)
        return ConversationHandler.END
    key = query.data.split(':', 1)[1]
    if not appearance.valid_item_key(key):
        return ConversationHandler.END
    ctx.user_data['appear_edit'] = {'key': key, 'mode': 'emoji'}
    await query.edit_message_text(
        '⭐ یک پیام بفرست که *ایموجی پریمیوم* داخلش باشد.\n'
        'همان ایموجی جلوی این گزینه برای کاربر نمایش داده می‌شود.\n\n'
        'صاحب ربات باید تلگرام پریمیوم داشته باشد تا ربات بتواند این ایموجی را نشان بدهد.\n'
        '/cancel برای انصراف',
        parse_mode='Markdown',
    )
    return WAIT_EMOJI


async def appear_text_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return ConversationHandler.END
    edit = ctx.user_data.get('appear_edit') or {}
    key = edit.get('key')
    if not key or not appearance.valid_item_key(key):
        return ConversationHandler.END
    value = (update.message.text or '').strip()
    if not value:
        await update.message.reply_text('❌ متن خالی مجاز نیست.')
        return WAIT_TEXT
    limit = appearance.max_len_for(key)
    if len(value) > limit:
        await update.message.reply_text(f'❌ متن از {limit} نویسه بیشتر است.')
        return WAIT_TEXT
    upsert_appearance(key, text=value)
    appearance.invalidate_cache()
    ctx.user_data.pop('appear_edit', None)
    await update.message.reply_text(
        '✅ متن ذخیره شد.\n\n' + _item_text(key),
        reply_markup=_item_kb(key),
    )
    return ConversationHandler.END


async def appear_emoji_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _guard(update):
        return ConversationHandler.END
    edit = ctx.user_data.get('appear_edit') or {}
    key = edit.get('key')
    if not key or not appearance.valid_item_key(key):
        return ConversationHandler.END
    found = appearance.extract_custom_emoji(update.message)
    if not found:
        await update.message.reply_text(
            '❌ در این پیام ایموجی پریمیوم پیدا نشد. یک پیام با ایموجی پریمیوم بفرست.'
        )
        return WAIT_EMOJI
    upsert_appearance(
        key,
        emoji_id=found['emoji_id'],
        emoji_char=found['emoji_char'],
    )
    appearance.invalidate_cache()
    ctx.user_data.pop('appear_edit', None)
    await update.message.reply_text(
        '✅ ایموجی پریمیوم ذخیره شد.\n\n' + _item_text(key),
        reply_markup=_item_kb(key),
    )
    return ConversationHandler.END


async def appear_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('appear_edit', None)
    await update.message.reply_text('انصراف از ویرایش ظاهر.', reply_markup=admin_home_keyboard())
    return ConversationHandler.END


def appearance_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(appear_text_start, pattern=r'^ap_txt:.+$'),
            CallbackQueryHandler(appear_emoji_start, pattern=r'^ap_emo:.+$'),
        ],
        states={
            WAIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, appear_text_receive)
            ],
            WAIT_EMOJI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, appear_emoji_receive)
            ],
        },
        fallbacks=[CommandHandler('cancel', appear_cancel)],
        allow_reentry=True,
    )
