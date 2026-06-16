import os
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

from handlers.start import start_handler, help_handler
from handlers.store import store_menu, show_category, show_product
from handlers.gems import gems_menu, show_gem
from handlers.sensitivity import sens_menu, sens_mobile_menu, show_sens_packs
from handlers.cart import show_cart, add_to_cart, clear_cart
from handlers.payment import checkout, verify
from handlers.support import support_menu, support_conversation_handler
from handlers.account import my_account, my_orders

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

MENU_TEXTS = {
    '🛍 فروشگاه': store_menu,
    '💎 جم فری‌فایر': gems_menu,
    '🎯 پک سنس': sens_menu,
    '🛒 سبد خرید': show_cart,
    '📦 سفارش‌های من': my_orders,
    '🎧 پشتیبانی': support_menu,
    '👤 حساب من': my_account,
}


async def text_router(update, ctx):
    text = update.message.text
    handler = MENU_TEXTS.get(text)
    if handler:
        await handler(update, ctx)
    else:
        await update.message.reply_text("❓ متوجه نشدم. از منوی پایین انتخاب کن.")


def main():
    token = os.getenv('BOT_TOKEN')
    if not token or token == 'YOUR_TOKEN_HERE':
        raise RuntimeError("توکن ربات رو در .env تنظیم کن: BOT_TOKEN=...")

    app = ApplicationBuilder().token(token).build()

    # Commands
    app.add_handler(CommandHandler('start', start_handler))
    app.add_handler(CommandHandler('help', help_handler))

    # ConversationHandler برای تیکت (باید قبل از callback های عام باشه)
    app.add_handler(support_conversation_handler())

    # Callback handlers — ترتیب مهمه (خاص‌تر اول)
    app.add_handler(CallbackQueryHandler(show_category, pattern='^cat_'))
    app.add_handler(CallbackQueryHandler(show_product, pattern='^prod_'))
    app.add_handler(CallbackQueryHandler(show_gem, pattern='^gem_'))
    app.add_handler(CallbackQueryHandler(sens_mobile_menu, pattern='^sens_mobile$'))
    app.add_handler(CallbackQueryHandler(show_sens_packs, pattern='^sens_'))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern='^add_'))
    app.add_handler(CallbackQueryHandler(show_cart, pattern='^cart$'))
    app.add_handler(CallbackQueryHandler(clear_cart, pattern='^cart_clear$'))
    app.add_handler(CallbackQueryHandler(checkout, pattern='^checkout$'))
    app.add_handler(CallbackQueryHandler(verify, pattern='^verify_pay$'))
    app.add_handler(CallbackQueryHandler(support_menu, pattern='^support$'))
    app.add_handler(CallbackQueryHandler(my_orders, pattern='^my_orders$'))
    app.add_handler(CallbackQueryHandler(my_account, pattern='^my_account$'))
    app.add_handler(CallbackQueryHandler(store_menu, pattern='^store$'))
    app.add_handler(CallbackQueryHandler(gems_menu, pattern='^gems$'))
    app.add_handler(CallbackQueryHandler(sens_menu, pattern='^sens$'))

    # Text message router
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logging.info("Bot Atomic Shop started!")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
