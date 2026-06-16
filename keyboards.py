from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


# ─── Main Menu (Reply Keyboard) ────────────────────────────────────────────────
def main_menu():
    return ReplyKeyboardMarkup([
        ['🛍 فروشگاه', '💎 جم فری‌فایر'],
        ['🎯 پک سنس', '🛒 سبد خرید'],
        ['📦 سفارش‌های من', '🎧 پشتیبانی'],
        ['👤 حساب من'],
    ], resize_keyboard=True)


# ─── Categories ─────────────────────────────────────────────────────────────────
def category_keyboard(categories):
    buttons = []
    row = []
    for i, cat in enumerate(categories):
        row.append(InlineKeyboardButton(cat[1], callback_data=f'cat_{cat[0]}'))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton('📦 همه محصولات', callback_data='cat_all')])
    return InlineKeyboardMarkup(buttons)


# ─── Product List ────────────────────────────────────────────────────────────────
def products_keyboard(products, back_data='store'):
    buttons = []
    for p in products:
        name = p[1]
        price = f"{p[2]:,}"
        badge = f" [{p[4]}]" if p[4] else ""
        buttons.append([
            InlineKeyboardButton(f"{name}{badge} — {price} ت", callback_data=f'product_{p[0]}')
        ])
    buttons.append([InlineKeyboardButton('🔙 برگشت', callback_data=back_data)])
    return InlineKeyboardMarkup(buttons)


# ─── Product Detail ──────────────────────────────────────────────────────────────
def product_detail_keyboard(product_id, back_data='store'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🛒 افزودن به سبد خرید', callback_data=f'add_p_{product_id}')],
        [InlineKeyboardButton('🔙 برگشت به لیست', callback_data=back_data)],
    ])


# ─── Gem Packages ────────────────────────────────────────────────────────────────
def gems_keyboard(gems):
    buttons = []
    for g in gems:
        title = g[1]
        price = f"{g[4]:,}"
        bonus = f" +{g[3]}" if g[3] else ""
        buttons.append([
            InlineKeyboardButton(f"💎 {title}{bonus} — {price} ت", callback_data=f'gem_{g[0]}')
        ])
    buttons.append([InlineKeyboardButton('🔙 برگشت', callback_data='back_main')])
    return InlineKeyboardMarkup(buttons)


# ─── Gem Detail ──────────────────────────────────────────────────────────────────
def gem_detail_keyboard(gem_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🛒 افزودن به سبد', callback_data=f'add_g_{gem_id}')],
        [InlineKeyboardButton('🔙 برگشت', callback_data='gems')],
    ])


# ─── Sensitivity Packs ───────────────────────────────────────────────────────────
def sens_platform_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📱 موبایل', callback_data='sens_mobile'),
            InlineKeyboardButton('🖥 پی‌سی', callback_data='sens_pc'),
        ],
        [InlineKeyboardButton('🔙 برگشت', callback_data='back_main')],
    ])


def sens_device_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📱 شیائومی', callback_data='sens_mob_xiaomi'),
            InlineKeyboardButton('📱 سامسونگ', callback_data='sens_mob_samsung'),
        ],
        [
            InlineKeyboardButton('🍎 آیفون', callback_data='sens_mob_iphone'),
            InlineKeyboardButton('📱 سایر اندروید', callback_data='sens_mob_android_other'),
        ],
        [InlineKeyboardButton('📦 همه موبایل', callback_data='sens_mob_all')],
        [InlineKeyboardButton('🔙 برگشت', callback_data='sens')],
    ])


def sens_packs_keyboard(packs, back_data='sens_mobile'):
    buttons = []
    for p in packs:
        price = f"{p[2]:,}"
        badge = f" [{p[4]}]" if p[4] else ""
        buttons.append([
            InlineKeyboardButton(f"🎯 {p[1]}{badge} — {price} ت", callback_data=f'add_s_{p[0]}')
        ])
    buttons.append([InlineKeyboardButton('🔙 برگشت', callback_data=back_data)])
    return InlineKeyboardMarkup(buttons)


# ─── Cart ────────────────────────────────────────────────────────────────────────
def cart_keyboard(has_items=True):
    if has_items:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('💳 پرداخت', callback_data='checkout')],
            [InlineKeyboardButton('🗑 خالی کردن سبد', callback_data='clear_cart')],
            [InlineKeyboardButton('🛍 ادامه خرید', callback_data='store')],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🛍 رفتن به فروشگاه', callback_data='store')],
    ])


# ─── Checkout / Payment ──────────────────────────────────────────────────────────
def checkout_keyboard(order_id, pay_url):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💳 پرداخت آنلاین', url=pay_url)],
        [InlineKeyboardButton('✅ تایید پرداخت', callback_data=f'verify_{order_id}')],
        [InlineKeyboardButton('❌ انصراف', callback_data='cancel_order')],
    ])


# ─── Support ─────────────────────────────────────────────────────────────────────
def support_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📩 ارسال تیکت جدید', callback_data='new_ticket')],
        [InlineKeyboardButton('🔙 برگشت', callback_data='back_main')],
    ])


def support_category_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💳 مشکل پرداخت', callback_data='ticket_cat_payment')],
        [InlineKeyboardButton('📦 مشکل سفارش', callback_data='ticket_cat_order')],
        [InlineKeyboardButton('🔑 مشکل حساب', callback_data='ticket_cat_account')],
        [InlineKeyboardButton('❓ سایر', callback_data='ticket_cat_other')],
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('❌ انصراف', callback_data='back_main')]
    ])
