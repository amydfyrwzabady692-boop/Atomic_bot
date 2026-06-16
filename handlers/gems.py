from telegram import Update
from telegram.ext import ContextTypes
from keyboards import gems_keyboard, gem_detail_keyboard, main_menu
from db import get_gems, get_gem


async def gems_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gems = get_gems()
    text = (
        "💎 *جم فری‌فایر*\n\n"
        "بسته الماس مورد نظرت رو انتخاب کن:\n"
        "_(تحویل فوری بعد از پرداخت)_"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=gems_keyboard(gems)
        )
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=gems_keyboard(gems))


async def show_gem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pk = int(query.data.split('_')[1])
    g = get_gem(pk)
    if not g:
        await query.edit_message_text("❌ بسته پیدا نشد.")
        return

    price_line = f"💰 *{g[4]:,} تومان*"
    if g[5] and g[5] > g[4]:
        price_line = f"~~{g[5]:,}~~ ➡️ 💰 *{g[4]:,} تومان*"

    bonus = f" + {g[3]} هدیه" if g[3] else ""
    purchase = "UID بازی" if g[6] == 'uid' else "اکانت فری‌فایر"
    text = (
        f"💎 *{g[1]}*\n\n"
        f"🔢 مقدار: {g[2]} الماس{bonus}\n"
        f"{price_line}\n"
        f"📋 روش تحویل: {purchase}\n\n"
        f"_برای خرید روی «افزودن به سبد» بزن_"
    )
    await query.edit_message_text(text, parse_mode='Markdown',
                                   reply_markup=gem_detail_keyboard(pk))
