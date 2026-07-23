"""نمایش محصولات مدیریت‌شده فروشگاه."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from db import get_setting, simple_list
from keyboards import main_menu


def _categories_keyboard():
    rows = simple_list('ProductCategories', ['Id', 'Title', 'IsActive'])
    buttons = [
        [InlineKeyboardButton(r[1], callback_data=f'storecat_{r[0]}')]
        for r in rows if r[2]
    ]
    buttons.append([InlineKeyboardButton('🔙 منوی اصلی', callback_data='home')])
    return InlineKeyboardMarkup(buttons)


async def store_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    title = get_setting('shop_name', 'فروشگاه Atomic')
    active = [r for r in simple_list(
        'ProductCategories', ['Id', 'Title', 'IsActive']
    ) if r[2]]
    text = f"🛍 *{title}*\n━━━━━━━━━━━━━━━\n"
    text += "دسته‌بندی را انتخاب کن:" if active else "فعلاً محصول فعالی ثبت نشده است."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=_categories_keyboard()
        )
    else:
        await update.message.reply_text(
            text, parse_mode='Markdown', reply_markup=_categories_keyboard() if active else main_menu()
        )


async def show_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.rsplit('_', 1)[1])
    products = [
        r for r in simple_list(
            'StoreProducts', ['Id', 'CategoryId', 'Title', 'Price', 'Stock', 'IsActive']
        ) if r[1] == category_id and r[5]
    ]
    buttons = [[InlineKeyboardButton(
        f'{p[2]} · {p[3]:,} ت', callback_data=f'storeprod_{p[0]}'
    )] for p in products]
    buttons.append([InlineKeyboardButton('🔙 دسته‌بندی‌ها', callback_data='store')])
    text = '📦 محصولات\n━━━━━━━━━━━━━━━\n'
    text += 'یک محصول را انتخاب کن:' if products else 'محصول فعالی در این دسته نیست.'
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.rsplit('_', 1)[1])
    row = next((r for r in simple_list(
        'StoreProducts',
        ['Id', 'CategoryId', 'Title', 'Price', 'Stock', 'Description', 'IsActive']
    ) if r[0] == product_id), None)
    if not row or not row[6]:
        await query.edit_message_text('محصول پیدا نشد.')
        return
    text = (
        f'📦 *{row[2]}*\n━━━━━━━━━━━━━━━\n'
        f'قیمت: *{row[3]:,} تومان*\nموجودی: *{row[4]}*\n\n{row[5] or "—"}\n\n'
        'برای خرید این محصول با پشتیبانی تماس بگیر.'
    )
    await query.edit_message_text(
        text, parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('🎧 پشتیبانی', callback_data='support')],
            [InlineKeyboardButton('🔙 بازگشت', callback_data=f'storecat_{row[1]}')],
        ]),
    )
