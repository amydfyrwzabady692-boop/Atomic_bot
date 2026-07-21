from telegram import Update
from telegram.ext import ContextTypes
from keyboards import updating_keyboard, main_menu


_UPDATING = (
    "🚧 *در حال بروزرسانی*\n"
    "━━━━━━━━━━━━━━━\n"
    "تیکت پشتیبانی ربات به‌زودی فعال می‌شود.\n"
    "فعلاً با ادمین در ارتباط باش."
)


async def support_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            _UPDATING, parse_mode='Markdown', reply_markup=updating_keyboard()
        )
    else:
        await update.message.reply_text(
            _UPDATING, parse_mode='Markdown', reply_markup=main_menu()
        )


def support_conversation_handler():
    """غیرفعال — برای سازگاری با bot.py یک ConversationHandler خالی برنمی‌گردانیم.
    bot.py دیگر این را ثبت نمی‌کند."""
    from telegram.ext import ConversationHandler
    return ConversationHandler(entry_points=[], states={}, fallbacks=[])
