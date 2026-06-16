from telegram import Update
from telegram.ext import ContextTypes
from keyboards import sens_platform_keyboard, sens_device_keyboard, sens_packs_keyboard
from db import get_sensitivity_packs


async def sens_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎯 *پک سنس هدشات*\n\n"
        "سنسیتیویتی حرفه‌ای برای فری‌فایر\n"
        "پلتفرم خودت رو انتخاب کن:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=sens_platform_keyboard()
        )
    else:
        await update.message.reply_text(text, parse_mode='Markdown',
                                        reply_markup=sens_platform_keyboard())


async def sens_mobile_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📱 *موبایل* — دستگاه خودت رو انتخاب کن:",
        parse_mode='Markdown',
        reply_markup=sens_device_keyboard()
    )


async def show_sens_packs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # data: sens_pc  OR  sens_mob_xiaomi  OR  sens_mob_all
    data = query.data

    if data == 'sens_pc':
        packs = get_sensitivity_packs(platform='pc')
        title = "🖥 *پک سنس پی‌سی*"
        back = 'sens'
    else:
        device = data.replace('sens_mob_', '')  # xiaomi / samsung / iphone / android_other / all
        packs = get_sensitivity_packs(platform='mobile', device=device if device != 'all' else None)
        device_names = {
            'xiaomi': 'شیائومی', 'samsung': 'سامسونگ',
            'iphone': 'آیفون', 'android_other': 'سایر اندروید', 'all': 'همه موبایل'
        }
        title = f"📱 *پک سنس {device_names.get(device, 'موبایل')}*"
        back = 'sens_mobile'

    if not packs:
        from keyboards import cancel_keyboard
        await query.edit_message_text(
            f"{title}\n\n❌ پکی در این دسته موجود نیست. به‌زودی اضافه میشه!",
            parse_mode='Markdown', reply_markup=cancel_keyboard()
        )
        return

    text = f"{title}\n\nیه پک انتخاب کن (با زدن روی اسمش به سبد اضافه میشه):"
    await query.edit_message_text(text, parse_mode='Markdown',
                                   reply_markup=sens_packs_keyboard(packs, back_data=back))
