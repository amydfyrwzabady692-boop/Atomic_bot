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
from handlers.gems import gems_menu, gem_choose_type, gem_filter_plan, show_gem, gem_conversation_handler
from handlers.sensitivity import sens_menu, sens_mobile_menu, show_sens_packs
from handlers.cart import show_cart, add_to_cart, clear_cart
from handlers.payment import checkout, cancel_order, payment_conversation_handler
from handlers.support import support_menu, support_conversation_handler
from handlers.account import my_account, my_orders

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

MENU_TEXTS = {
    '🛍 فروشگاه اکانت': store_menu,
    '💎 جم فری‌فایر': gems_menu,
    '🎯 پک سنس': sens_menu,
    '🛒 سبد خرید': show_cart,
    '📦 سفارش‌های من': my_orders,
    '🎧 پشتیبانی': support_menu,
    '👤 حساب من': my_account,
}


async def text_router(update, ctx):
    handler = MENU_TEXTS.get(update.message.text)
    if handler:
        await handler(update, ctx)
    else:
        await update.message.reply_text("❓ متوجه نشدم. از منوی پایین انتخاب کن 👇")


def main():
    token = os.getenv('BOT_TOKEN')
    if not token or token == 'YOUR_TOKEN_HERE':
        raise RuntimeError("توکن ربات رو در .env تنظیم کن: BOT_TOKEN=...")

    app = ApplicationBuilder().token(token).build()

    # دستورها
    app.add_handler(CommandHandler('start', start_handler))
    app.add_handler(CommandHandler('help', help_handler))

    # گفتگوها (باید قبل از هندلرهای عمومی باشند)
    app.add_handler(support_conversation_handler())
    app.add_handler(gem_conversation_handler())
    app.add_handler(payment_conversation_handler())

    # ─── فروشگاه ───
    app.add_handler(CallbackQueryHandler(show_category, pattern=r'^cat_'))
    app.add_handler(CallbackQueryHandler(show_product, pattern=r'^prod_\d+$'))
    # ─── جم ───
    app.add_handler(CallbackQueryHandler(gems_menu, pattern=r'^gems$'))
    app.add_handler(CallbackQueryHandler(gem_choose_type, pattern=r'^gtype_'))
    app.add_handler(CallbackQueryHandler(gem_filter_plan, pattern=r'^gp_'))
    app.add_handler(CallbackQueryHandler(show_gem, pattern=r'^gem_\d+$'))
    # ─── پک سنس ───
    app.add_handler(CallbackQueryHandler(sens_mobile_menu, pattern=r'^sens_mobile$'))
    app.add_handler(CallbackQueryHandler(show_sens_packs, pattern=r'^sens_(pc|mob_)'))
    app.add_handler(CallbackQueryHandler(sens_menu, pattern=r'^sens$'))
    # ─── سبد و پرداخت ───
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern=r'^add_[ps]_\d+$'))
    app.add_handler(CallbackQueryHandler(show_cart, pattern=r'^cart$'))
    app.add_handler(CallbackQueryHandler(clear_cart, pattern=r'^cart_clear$'))
    app.add_handler(CallbackQueryHandler(checkout, pattern=r'^checkout$'))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r'^cancel_order$'))
    # ─── سایر ───
    app.add_handler(CallbackQueryHandler(support_menu, pattern=r'^support$'))
    app.add_handler(CallbackQueryHandler(my_orders, pattern=r'^my_orders$'))
    app.add_handler(CallbackQueryHandler(my_account, pattern=r'^my_account$'))
    app.add_handler(CallbackQueryHandler(store_menu, pattern=r'^store$'))

    # روتر پیام متنی منو
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logging.info("Atomic Shop bot started!")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
