from telegram import Update
from telegram.ext import ContextTypes
from keyboards import updating_keyboard, main_menu


_UPDATING = (
    "🚧 *در حال بروزرسانی*\n"
    "━━━━━━━━━━━━━━━\n"
    "بخش پک سنس فعلاً غیرفعال است.\n"
    "از منوی *جم فری‌فایر* خرید کن 💎"
)


async def sens_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            _UPDATING, parse_mode='Markdown', reply_markup=updating_keyboard()
        )
    else:
        await update.message.reply_text(
            _UPDATING, parse_mode='Markdown', reply_markup=main_menu()
        )


async def sens_mobile_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await sens_menu(update, ctx)


async def show_sens_packs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await sens_menu(update, ctx)
