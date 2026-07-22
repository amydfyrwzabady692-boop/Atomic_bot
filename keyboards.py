from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def _fmt(n):
    return f"{n:,}"


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ['💎 جم فری‌فایر', '💰 کیف پول'],
            ['📦 سفارش‌های من', '👤 حساب من'],
            ['🛍 فروشگاه اکانت', '🎯 پک سنس'],
            ['🎧 پشتیبانی'],
        ],
        resize_keyboard=True,
        input_field_placeholder='از منوی پایین انتخاب کن…',
    )


def updating_keyboard(back='home'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('بازگشت', callback_data=back)],
    ])


def sens_platform_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🖥 PC', callback_data='sens_pc')],
        [InlineKeyboardButton('📱 موبایل', callback_data='sens_mobile')],
        [InlineKeyboardButton('منوی اصلی', callback_data='home')],
    ])


def sens_pc_packs_keyboard(packs):
    rows = []
    for key, p in packs.items():
        rows.append([
            InlineKeyboardButton(
                f"{p['title']} — {p['price']:,} ت",
                callback_data=f"sens_buy_{key}",
            )
        ])
    rows.append([InlineKeyboardButton('بازگشت', callback_data='sens')])
    return InlineKeyboardMarkup(rows)


def gems_list_keyboard(gems):
    buttons = []
    for g in gems:
        # Id, Title, Amount, BonusAmount, Price, ...
        total = g[2] + (g[3] or 0)
        auto = '⚡️' if g[8] else ''
        sold_out = (not g[8] and (g[10] or 0) <= 0) or (g[11] is False)
        label = f"{auto}💎 {g[1]} • {_fmt(total)} • {_fmt(g[4])} ت"
        if sold_out and not g[8]:
            label = f"❌ ناموجود — {g[1]}"
            buttons.append([InlineKeyboardButton(label, callback_data='noop')])
        else:
            buttons.append([InlineKeyboardButton(label, callback_data=f'gem_{g[0]}')])
    buttons.append([InlineKeyboardButton('🔙 منوی اصلی', callback_data='home')])
    return InlineKeyboardMarkup(buttons)


def gem_detail_keyboard(gem_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ خرید این بسته', callback_data=f'gbuy_{gem_id}')],
        [InlineKeyboardButton('🔙 بازگشت به لیست', callback_data='gems')],
    ])


def gem_cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✖️ انصراف', callback_data='gem_cancel')],
    ])


def gem_confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ تایید و ادامه پرداخت', callback_data='gem_confirm')],
        [InlineKeyboardButton('✏️ اصلاح آیدی', callback_data='gem_reedit')],
        [InlineKeyboardButton('✖️ انصراف', callback_data='gem_cancel')],
    ])


def pay_method_keyboard(order_id, can_wallet=True):
    rows = [
        [InlineKeyboardButton('💳 زرین‌پال', callback_data=f'pay_zp_{order_id}')],
        [InlineKeyboardButton('🏧 کارت‌به‌کارت', callback_data=f'pay_card_{order_id}')],
    ]
    if can_wallet:
        rows.append([InlineKeyboardButton('💰 کیف پول', callback_data=f'pay_wallet_{order_id}')])
    rows.append([InlineKeyboardButton('انصراف', callback_data=f'cancel_order_{order_id}')])
    return InlineKeyboardMarkup(rows)


def zarinpal_pay_keyboard(order_id, pay_url=None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ پرداخت کردم', callback_data=f'zp_check_{order_id}')],
        [InlineKeyboardButton('انصراف', callback_data=f'cancel_order_{order_id}')],
    ])


def card_payment_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ پرداخت کردم — ارسال رسید', callback_data=f'paid_done_{order_id}')],
        [InlineKeyboardButton('انصراف', callback_data=f'cancel_order_{order_id}')],
    ])


def receipt_skip_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('⏭ بدون رسید ادامه بده', callback_data=f'paid_skip_{order_id}')],
        [InlineKeyboardButton('❌ انصراف', callback_data=f'cancel_order_{order_id}')],
    ])


def admin_card_keyboard(order_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('✅ تایید سفارش', callback_data=f'admin_ok_{order_id}'),
            InlineKeyboardButton('❌ رد سفارش', callback_data=f'admin_no_{order_id}'),
        ],
    ])


def wallet_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('۵۰٬۰۰۰ ت', callback_data='wchg_50000'),
            InlineKeyboardButton('۱۰۰٬۰۰۰ ت', callback_data='wchg_100000'),
        ],
        [
            InlineKeyboardButton('۲۰۰٬۰۰۰ ت', callback_data='wchg_200000'),
            InlineKeyboardButton('۵۰۰٬۰۰۰ ت', callback_data='wchg_500000'),
        ],
        [InlineKeyboardButton('✏️ مبلغ دلخواه', callback_data='wchg_custom')],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='home')],
    ])


def wallet_charge_pay_keyboard(tx_key, pay_url=None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ پرداخت کردم', callback_data=f'wchk_{tx_key}')],
        [InlineKeyboardButton('بازگشت به کیف پول', callback_data='wallet')],
    ])


def support_cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('❌ انصراف', callback_data='support_cancel')],
    ])


def admin_home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('کاربران', callback_data='adm_users'),
            InlineKeyboardButton('جستجو', callback_data='adm_find'),
        ],
        [
            InlineKeyboardButton('تحویل ناموفق', callback_data='adm_failed'),
            InlineKeyboardButton('سفارش‌های باز', callback_data='adm_open'),
        ],
        [InlineKeyboardButton('تیکت‌ها', callback_data='adm_tickets')],
        [InlineKeyboardButton('بروزرسانی', callback_data='adm_home')],
    ])


def admin_user_keyboard(tg_id, is_blocked=False):
    block_btn = (
        InlineKeyboardButton('آنبلاک', callback_data=f'adm_block_0_{tg_id}')
        if is_blocked else
        InlineKeyboardButton('بلاک', callback_data=f'adm_block_1_{tg_id}')
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('پیام', callback_data=f'adm_msg_{tg_id}'),
            InlineKeyboardButton('کیف پول', callback_data=f'adm_wal_{tg_id}'),
        ],
        [
            InlineKeyboardButton('سفارش‌ها', callback_data=f'adm_ords_{tg_id}'),
            block_btn,
        ],
        [InlineKeyboardButton('بازگشت', callback_data='adm_home')],
    ])


def admin_failed_order_keyboard(order_id, tg_id=''):
    rows = [
        [InlineKeyboardButton('🔁 تلاش مجدد تحویل', callback_data=f'adm_retry_{order_id}')],
    ]
    if tg_id:
        rows.append([InlineKeyboardButton('👤 کارت کاربر', callback_data=f'adm_user_{tg_id}')])
    rows.append([InlineKeyboardButton('🔙 تحویل‌های ناموفق', callback_data='adm_failed')])
    return InlineKeyboardMarkup(rows)


def admin_ticket_keyboard(ticket_id, tg_id=None):
    rows = [
        [
            InlineKeyboardButton('💬 پاسخ', callback_data=f'adm_treply_{ticket_id}'),
            InlineKeyboardButton('✅ بستن تیکت', callback_data=f'adm_tclose_{ticket_id}'),
        ],
    ]
    if tg_id:
        rows.append([InlineKeyboardButton('👤 کارت کاربر', callback_data=f'adm_user_{tg_id}')])
    rows.append([InlineKeyboardButton('🛠 پنل ادمین', callback_data='adm_home')])
    return InlineKeyboardMarkup(rows)
