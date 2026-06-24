from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

# ════════════════════════════════════════════════════════════════════════════
#  Atomic Shop — صفحه‌کلیدها (طراحی یکپارچه)
# ════════════════════════════════════════════════════════════════════════════

PLAN_LABELS = {'once': 'تک‌خرید', 'weekly': 'هفتگی', 'monthly': 'ماهانه'}
PLAN_ICONS = {'once': '🛒', 'weekly': '🗓', 'monthly': '📆'}


def _fmt(n):
    return f"{n:,}"


# ─── منوی اصلی (Reply Keyboard) ─────────────────────────────────────────────────
def main_menu():
    return ReplyKeyboardMarkup(
        [
            ['🛍 فروشگاه اکانت', '💎 جم فری‌فایر'],
            ['🎯 پک سنس', '🛒 سبد خرید'],
            ['📦 سفارش‌های من', '👤 حساب من'],
            ['🎧 پشتیبانی'],
        ],
        resize_keyboard=True,
        input_field_placeholder='از منوی پایین انتخاب کن…',
    )


# ─── دسته‌بندی محصولات ───────────────────────────────────────────────────────────
def category_keyboard(categories):
    buttons, row = [], []
    for cat in categories:
        row.append(InlineKeyboardButton(f"📂 {cat[1]}", callback_data=f'cat_{cat[0]}'))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton('🗂 همه‌ی محصولات', callback_data='cat_all')])
    return InlineKeyboardMarkup(buttons)


def products_keyboard(products, back_data='store'):
    buttons = []
    for p in products:
        badge = f"🏷{p[4]} " if p[4] else ""
        buttons.append([InlineKeyboardButton(
            f"🎮 {badge}{p[1]} — {_fmt(p[2])} ت", callback_data=f'prod_{p[0]}'
        )])
    buttons.append([InlineKeyboardButton('🔙 بازگشت', callback_data=back_data)])
    return InlineKeyboardMarkup(buttons)


def product_detail_keyboard(product_id, back_data='store'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🛒 افزودن به سبد خرید', callback_data=f'add_p_{product_id}')],
        [InlineKeyboardButton('🔙 بازگشت به لیست', callback_data=back_data)],
    ])


# ─── جم فری‌فایر ─────────────────────────────────────────────────────────────────
def gem_type_keyboard():
    """انتخاب روش خرید جم."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🆔 خرید با آیدی بازی (UID)', callback_data='gtype_by_id')],
        [InlineKeyboardButton('🔐 خرید با اطلاعات اکانت', callback_data='gtype_by_credentials')],
    ])


def gem_plan_tabs(ptype, active_plan='all'):
    """ردیف فیلتر نوع پلن."""
    tabs = [('all', 'همه')] + [(k, PLAN_LABELS[k]) for k in ('once', 'weekly', 'monthly')]
    row = []
    for key, label in tabs:
        mark = '🔹' if key == active_plan else '▫️'
        row.append(InlineKeyboardButton(f"{mark} {label}", callback_data=f'gp_{ptype}_{key}'))
    # دو ردیف دوتایی برای زیبایی
    return [row[:2], row[2:]]


def gems_keyboard(gems, ptype, active_plan='all'):
    buttons = list(gem_plan_tabs(ptype, active_plan))
    for g in gems:
        # g = Id, Title, Amount, BonusAmount, Price, OldPrice, PlanType, PurchaseType
        total = g[2] + (g[3] or 0)
        plan_icon = PLAN_ICONS.get(g[6], '💎')
        buttons.append([InlineKeyboardButton(
            f"{plan_icon} {g[1]} • {_fmt(total)}💎 • {_fmt(g[4])} ت",
            callback_data=f'gem_{g[0]}'
        )])
    buttons.append([InlineKeyboardButton('🔙 تغییر روش خرید', callback_data='gems')])
    return InlineKeyboardMarkup(buttons)


def gem_detail_keyboard(gem_id, ptype):
    back = f'gp_{ptype}_all'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ ثبت سفارش این بسته', callback_data=f'gbuy_{gem_id}')],
        [InlineKeyboardButton('🔙 بازگشت به لیست', callback_data=back)],
    ])


def gem_login_method_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📧 جیمیل', callback_data='gm_gmail'),
            InlineKeyboardButton('📘 فیسبوک', callback_data='gm_facebook'),
            InlineKeyboardButton('🆅 VK', callback_data='gm_vk'),
        ],
        [InlineKeyboardButton('✖️ انصراف', callback_data='gem_cancel')],
    ])


def gem_skip_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('⏭ این مرحله را رد کن', callback_data='gskip')],
        [InlineKeyboardButton('✖️ انصراف', callback_data='gem_cancel')],
    ])


def gem_cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✖️ انصراف', callback_data='gem_cancel')],
    ])


def added_to_cart_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💳 تسویه و پرداخت', callback_data='checkout')],
        [InlineKeyboardButton('🛒 مشاهده سبد', callback_data='cart'),
         InlineKeyboardButton('➕ ادامه خرید', callback_data='gems')],
    ])


# ─── پک سنسیتیویتی ───────────────────────────────────────────────────────────────
def sens_platform_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📱 موبایل', callback_data='sens_mobile'),
            InlineKeyboardButton('🖥 پی‌سی', callback_data='sens_pc'),
        ],
    ])


def sens_device_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📱 شیائومی', callback_data='sens_mob_xiaomi'),
            InlineKeyboardButton('📱 سامسونگ', callback_data='sens_mob_samsung'),
        ],
        [
            InlineKeyboardButton('🍎 آیفون', callback_data='sens_mob_iphone'),
            InlineKeyboardButton('🤖 سایر اندروید', callback_data='sens_mob_android_other'),
        ],
        [InlineKeyboardButton('🗂 همه‌ی موبایل', callback_data='sens_mob_all')],
        [InlineKeyboardButton('🔙 بازگشت', callback_data='sens')],
    ])


def sens_packs_keyboard(packs, back_data='sens_mobile'):
    buttons = []
    for p in packs:
        badge = f"🏷{p[4]} " if p[4] else ""
        buttons.append([InlineKeyboardButton(
            f"🎯 {badge}{p[1]} — {_fmt(p[2])} ت", callback_data=f'add_s_{p[0]}'
        )])
    buttons.append([InlineKeyboardButton('🔙 بازگشت', callback_data=back_data)])
    return InlineKeyboardMarkup(buttons)


# ─── سبد خرید ────────────────────────────────────────────────────────────────────
def cart_keyboard(has_items=True):
    if has_items:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('💳 تسویه و پرداخت', callback_data='checkout')],
            [InlineKeyboardButton('🗑 خالی کردن سبد', callback_data='cart_clear')],
            [InlineKeyboardButton('🛍 ادامه خرید', callback_data='store')],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🛍 رفتن به فروشگاه', callback_data='store')],
        [InlineKeyboardButton('💎 خرید جم', callback_data='gems')],
    ])


# ─── پرداخت کارت‌به‌کارت ──────────────────────────────────────────────────────────
def card_payment_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ پرداخت کردم — ارسال رسید', callback_data='paid_done')],
        [InlineKeyboardButton('❌ انصراف', callback_data='cancel_order')],
    ])


def receipt_skip_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('⏭ بدون رسید ادامه بده', callback_data='paid_skip')],
        [InlineKeyboardButton('❌ انصراف', callback_data='cancel_order')],
    ])


# ─── پشتیبانی ────────────────────────────────────────────────────────────────────
def support_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📩 ارسال تیکت جدید', callback_data='new_ticket')],
    ])


def support_category_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💎 مشکل جم', callback_data='ticket_diamond'),
         InlineKeyboardButton('💳 مشکل پرداخت', callback_data='ticket_payment')],
        [InlineKeyboardButton('📦 مشکل سفارش', callback_data='ticket_order'),
         InlineKeyboardButton('🔑 مشکل اکانت', callback_data='ticket_account')],
        [InlineKeyboardButton('❓ سایر موارد', callback_data='ticket_other')],
        [InlineKeyboardButton('✖️ انصراف', callback_data='cancel')],
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✖️ انصراف', callback_data='cancel')]
    ])
