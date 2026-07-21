from telegram import Update
from telegram.ext import ContextTypes
from keyboards import updating_keyboard, main_menu


_UPDATING = (
    "🚧 *در حال بروزرسانی*\n"
    "━━━━━━━━━━━━━━━\n"
    "این بخش فعلاً غیرفعال است و به‌زودی برمی‌گردد.\n"
    "الان می‌تونی از *جم با آیدی* و *کیف پول* استفاده کنی 💎"
)


async def store_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            _UPDATING, parse_mode='Markdown', reply_markup=updating_keyboard()
        )
    else:
        await update.message.reply_text(
            _UPDATING, parse_mode='Markdown', reply_markup=main_menu()
        )


async def show_category(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await store_menu(update, ctx)


async def show_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await store_menu(update, ctx)
