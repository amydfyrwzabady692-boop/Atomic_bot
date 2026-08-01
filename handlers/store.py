"""نمایش محصولات مدیریت‌شده فروشگاه."""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from db import get_setting, simple_list
from keyboards import main_menu
from text_safety import markdown_safe


def _categories_keyboard(rows):
    buttons = [
        [InlineKeyboardButton(r[1], callback_data=f'storecat_{r[0]}')]
        for r in rows if r[2]
    ]
    buttons.append([InlineKeyboardButton('🔙 منوی اصلی', callback_data='home')])
    return InlineKeyboardMarkup(buttons)


async def store_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    title, category_rows = await asyncio.gather(
        asyncio.to_thread(get_setting, 'shop_name', 'فروشگاه Atomic'),
        asyncio.to_thread(
            simple_list, 'ProductCategories', ['Id', 'Title', 'IsActive']
        ),
    )
    active = [r for r in category_rows if r[2]]
    text = f"🛍 *{markdown_safe(title, 120)}*\n━━━━━━━━━━━━━━━\n"
    text += "دسته‌بندی را انتخاب کن:" if active else "فعلاً محصول فعالی ثبت نشده است."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=_categories_keyboard(category_rows)
        )
    else:
        await update.message.reply_text(
            text, parse_mode='Markdown',
            reply_markup=_categories_keyboard(category_rows) if active else main_menu(),
        )


async def show_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.rsplit('_', 1)[1])
    products = [
        r for r in await asyncio.to_thread(
            simple_list,
            'StoreProducts', ['Id', 'CategoryId', 'Title', 'Price', 'Stock', 'IsActive']
        ) if r[1] == category_id and r[5] and int(r[4] or 0) > 0
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
    rows = await asyncio.to_thread(
        simple_list,
        'StoreProducts',
        ['Id', 'CategoryId', 'Title', 'Price', 'Stock', 'Description', 'IsActive'],
    )
    row = next((r for r in rows if r[0] == product_id), None)
    if not row or not row[6] or int(row[4] or 0) <= 0:
        await query.edit_message_text('محصول پیدا نشد.')
        return
    text = (
        f'📦 *{markdown_safe(row[2], 120)}*\n━━━━━━━━━━━━━━━\n'
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
