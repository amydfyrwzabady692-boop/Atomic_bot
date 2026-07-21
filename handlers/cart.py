from telegram import Update
from telegram.ext import ContextTypes
from keyboards import updating_keyboard, main_menu


_UPDATING = (
    "🚧 *در حال بروزرسانی*\n"
    "━━━━━━━━━━━━━━━\n"
    "سبد خرید عمومی فعلاً غیرفعال است.\n"
    "خرید جم مستقیم از منوی *جم فری‌فایر* انجام می‌شود."
)


async def show_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            _UPDATING, parse_mode='Markdown', reply_markup=updating_keyboard()
        )
    else:
        await update.message.reply_text(
            _UPDATING, parse_mode='Markdown', reply_markup=main_menu()
        )


async def add_to_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_cart(update, ctx)


async def clear_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_cart(update, ctx)


# سازگاری با importهای قدیمی
def _get_cart(ctx):
    return ctx.user_data.setdefault('cart', {})


def _cart_total(cart):
    return 0


def cart_add(*args, **kwargs):
    return {}
