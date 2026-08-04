"""تنظیم یک‌باره توضیحات و منوی ربات تلگرام.

اجرا:  python setup_bot.py
باید در مسیر پروژه تلگرام اجرا شود تا .env خوانده شود.
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

DESCRIPTION = """🎮 اتومیک شاپ
⚛️ Atomic Shop

✨ خرید جم فری‌فایر با بهترین قیمت و تحویل آنی
🎯 پک‌های حرفه‌ای سنسیویتی موبایل و PC
💳 پرداخت امن با درگاه زرین‌پال یا کارت‌به‌کارت
💰 کیف پول هوشمند
🎁 کدهای هدیه و تخفیف‌های ویژه
🧑‍💻 پشتیبانی مستقیم و پیگیری سفارش

برای شروع، دکمه Start را بزنید 👇"""

SHORT_DESCRIPTION = "💎 خرید جم فری‌فایر و پک سنسیویتی | پرداخت امن"

COMMANDS = [
    ("products", "🎮 محصولات"),
    ("wallet", "💰 کیف پول"),
    ("orders", "📦 سفارش‌ها"),
    ("account", "👤 حساب کاربری"),
    ("store", "🛍 فروشگاه"),
    ("sense", "🎯 پک سنس"),
    ("support", "🎧 پشتیبانی"),
]


async def main():
    token = os.getenv('BOT_TOKEN', '').strip()
    if not token or token.startswith('YOUR'):
        print("❌ BOT_TOKEN در .env تنظیم نشده است.")
        return
    from telegram import Bot, BotCommand
    bot = Bot(token=token)
    me = await bot.get_me()
    print("ربات:", me.first_name or me.username)
    await bot.set_my_description(DESCRIPTION)
    await bot.set_my_short_description(SHORT_DESCRIPTION)
    await bot.set_my_commands([
        BotCommand(cmd, desc) for cmd, desc in COMMANDS
    ])
    print("✅ توضیحات، توضیح کوتاه و دستورات منو تنظیم شد.")
    print("ℹ️ کیبورد پاسخ (ReplyKeyboardMarkup) هنگام /start توسط ربات ارسال می‌شود.")


if __name__ == "__main__":
    asyncio.run(main())
