from telegram import Update
from telegram.ext import ContextTypes
from keyboards import category_keyboard, products_keyboard, product_detail_keyboard, main_menu
from db import get_categories, get_products, get_product


async def store_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    categories = get_categories()
    msg = update.message or update.callback_query.message
    text = "🛍 *فروشگاه Atomic Shop*\nیه دسته‌بندی انتخاب کن:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown',
                                                       reply_markup=category_keyboard(categories))
    else:
        await msg.reply_text(text, parse_mode='Markdown', reply_markup=category_keyboard(categories))


async def show_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # cat_5 or cat_all

    cat_id = None if data == 'cat_all' else int(data.split('_')[1])
    products = get_products(category_id=cat_id)

    if not products:
        await query.edit_message_text("❌ محصولی در این دسته یافت نشد.", reply_markup=None)
        return

    text = f"📦 *{len(products)} محصول یافت شد*\nیکی رو انتخاب کن:"
    await query.edit_message_text(text, parse_mode='Markdown',
                                   reply_markup=products_keyboard(products, back_data='store'))


async def show_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pk = int(query.data.split('_')[1])
    p = get_product(pk)

    if not p:
        await query.edit_message_text("❌ محصول پیدا نشد.")
        return

    price_line = f"💰 *{p[2]:,} تومان*"
    if p[3] and p[3] > p[2]:
        price_line = f"~~{p[3]:,}~~ ➡️ 💰 *{p[2]:,} تومان*"

    badge = f"\n🏷 {p[4]}" if p[4] else ""
    details = f"\n\n📝 {p[6]}" if p[6] else ""
    text = (
        f"🎮 *{p[1]}*{badge}\n\n"
        f"{price_line}{details}"
    )
    await query.edit_message_text(
        text, parse_mode='Markdown',
        reply_markup=product_detail_keyboard(pk, back_data='store')
    )
