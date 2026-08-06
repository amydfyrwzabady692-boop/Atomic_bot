from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

GEM_PRODUCTS_PER_PAGE = 8


def _fmt(n):
    return f"{n:,}"


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ['🎮 محصولات فری‌فایر', '💰 کیف پول'],
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
    values = packs.values() if isinstance(packs, dict) else packs
    for p in values:
        if isinstance(p, dict):
            key, title, price = p['key'], p['title'], p['price']
        else:
            key, title, price = p[0], p[1], p[3]
        rows.append([
            InlineKeyboardButton(
                f"{title} — {price:,} ت",
                callback_data=f"sens_buy_{key}",
            )
        ])
    rows.append([InlineKeyboardButton('بازگشت', callback_data='sens')])
    return InlineKeyboardMarkup(rows)


def _gem_catalogue_order(gem):
    """Keep diamonds/memberships on page one and Level Up packs on page two."""
    catalogue_name = str(gem[9] or '').strip().casefold()
    return catalogue_name.startswith('level up package')


def ordered_gem_catalogue(gems):
    return sorted(gems, key=_gem_catalogue_order)


def gems_list_keyboard(gems, page=1, per_page=GEM_PRODUCTS_PER_PAGE):
    gems = ordered_gem_catalogue(gems)
    total_pages = max(1, (len(gems) + per_page - 1) // per_page)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * per_page
    buttons = []
    for g in gems[start:start + per_page]:
        # Id, Title, Amount, BonusAmount, Price, ...
        auto = '⚡️' if g[8] else ''
        sold_out = (not g[8] and (g[10] or 0) <= 0) or (g[11] is False)
        label = f"{auto} {g[1]}  •  {_fmt(g[4])} تومان"
        if sold_out and not g[8]:
            label = f"❌ ناموجود — {g[1]}"
            buttons.append([InlineKeyboardButton(label, callback_data='noop')])
        else:
            buttons.append([InlineKeyboardButton(label, callback_data=f'gem_{g[0]}')])
    if total_pages > 1:
        nav = [
            InlineKeyboardButton(
                f'• {number} •' if number == page else str(number),
                callback_data='noop' if number == page else f'gems_page_{number}',
            )
            for number in range(1, total_pages + 1)
        ]
        buttons.append(nav)
    buttons.append([InlineKeyboardButton('🔙 منوی اصلی', callback_data='home')])
    return InlineKeyboardMarkup(buttons)


def gem_detail_keyboard(gem_id, page=1):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ خرید این بسته', callback_data=f'gbuy_{gem_id}')],
        [InlineKeyboardButton(
            '🔙 بازگشت به لیست', callback_data=f'gems_page_{max(1, int(page))}'
        )],
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


def pay_method_keyboard(order_id, can_wallet=True, wallet_balance=0, remaining=None):
    """همیشه دکمه کیف پول را نشان بده (حتی با موجودی صفر) تا کاربر بداند این روش هست."""
    bal = int(wallet_balance or 0)
    rem = int(remaining) if remaining is not None else None
    rows = [
        [InlineKeyboardButton('💳 زرین‌پال', callback_data=f'pay_zp_{order_id}')],
        [InlineKeyboardButton('🏧 کارت‌به‌کارت', callback_data=f'pay_card_{order_id}')],
    ]
    if can_wallet and (rem is None or rem > 0):
        if bal <= 0:
            label = '💰 کیف پول (موجودی صفر)'
        elif rem is not None and bal >= rem:
            label = '💰 پرداخت کامل از کیف پول'
        else:
            label = f'💰 استفاده از کیف پول ({bal:,} ت)'
        rows.append([InlineKeyboardButton(label, callback_data=f'pay_wallet_{order_id}')])
    rows.append([InlineKeyboardButton('انصراف', callback_data=f'cancel_order_{order_id}')])
    return InlineKeyboardMarkup(rows)


def wallet_charge_method_keyboard(amount):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💳 درگاه زرین‌پال', callback_data=f'wpay_zp_{amount}')],
        [InlineKeyboardButton('🏧 کارت‌به‌کارت', callback_data=f'wpay_card_{amount}')],
        [InlineKeyboardButton('بازگشت', callback_data='wallet')],
    ])


def wallet_card_pay_keyboard(tx_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ پرداخت کردم — ارسال رسید', callback_data=f'wcard_done_{tx_key}')],
        [InlineKeyboardButton('بازگشت', callback_data='wallet')],
    ])


def admin_wallet_card_keyboard(tx_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                '✅ بررسی برای تأیید', callback_data=f'wadmin_review_ok_{tx_id}'
            ),
            InlineKeyboardButton(
                '❌ بررسی برای رد', callback_data=f'wadmin_review_no_{tx_id}'
            ),
        ],
    ])


def admin_wallet_card_confirm_keyboard(tx_id, action):
    if action == 'ok':
        label, callback = '⚠️ تأیید نهایی و شارژ کیف پول', f'wadmin_ok_{tx_id}'
    else:
        label, callback = '⚠️ رد نهایی رسید', f'wadmin_no_{tx_id}'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=callback)],
        [InlineKeyboardButton('🔙 بازگشت', callback_data=f'wadmin_review_back_{tx_id}')],
    ])


def zarinpal_pay_keyboard(order_id, pay_url=None):
    rows = []
    if pay_url:
        rows.append([InlineKeyboardButton('🔗 باز کردن درگاه پرداخت', url=pay_url)])
    rows.extend([
        [InlineKeyboardButton('✅ پرداخت کردم', callback_data=f'zp_check_{order_id}')],
        [InlineKeyboardButton('🔄 تغییر امن روش پرداخت', callback_data=f'change_pay_{order_id}')],
    ])
    return InlineKeyboardMarkup(rows)


def card_payment_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ پرداخت کردم — ارسال رسید', callback_data=f'paid_done_{order_id}')],
        [InlineKeyboardButton('انصراف', callback_data=f'cancel_order_{order_id}')],
    ])


def receipt_skip_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('❌ انصراف از پرداخت', callback_data=f'cancel_order_{order_id}')],
    ])


def admin_card_keyboard(order_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                '✅ بررسی برای تأیید', callback_data=f'admin_review_ok_{order_id}'
            ),
            InlineKeyboardButton(
                '❌ بررسی برای رد', callback_data=f'admin_review_no_{order_id}'
            ),
        ],
    ])


def admin_card_confirm_keyboard(order_id, action):
    if action == 'ok':
        label, callback = '⚠️ تأیید نهایی و شروع تحویل', f'admin_ok_{order_id}'
    else:
        label, callback = '⚠️ رد نهایی و لغو سفارش', f'admin_no_{order_id}'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=callback)],
        [InlineKeyboardButton('🔙 بازگشت', callback_data=f'admin_review_back_{order_id}')],
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
    rows = []
    if pay_url:
        rows.append([InlineKeyboardButton('🔗 باز کردن درگاه پرداخت', url=pay_url)])
    rows.extend([
        [InlineKeyboardButton('✅ پرداخت کردم', callback_data=f'wchk_{tx_key}')],
        [InlineKeyboardButton('بازگشت به کیف پول', callback_data='wallet')],
    ])
    return InlineKeyboardMarkup(rows)


def support_cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('❌ انصراف', callback_data='support_cancel')],
    ])


def admin_home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🚨 مرکز عملیات و هشدارها', callback_data='admx_ops')],
        [
            InlineKeyboardButton('کاربران', callback_data='adm_users'),
            InlineKeyboardButton('جستجو', callback_data='adm_find'),
        ],
        [
            InlineKeyboardButton('تحویل ناموفق', callback_data='adm_failed'),
            InlineKeyboardButton('سفارش‌های باز', callback_data='adm_open'),
        ],
        [InlineKeyboardButton('تیکت‌ها', callback_data='adm_tickets')],
        [
            InlineKeyboardButton('🛍 مدیریت فروشگاه', callback_data='admx_shop'),
            InlineKeyboardButton('💳 امور مالی', callback_data='admx_finance'),
        ],
        [
            InlineKeyboardButton('📨 پیام و شارژ', callback_data='admx_actions'),
            InlineKeyboardButton('📊 گزارش و آمار', callback_data='admx_stats'),
        ],
        [
            InlineKeyboardButton('🎧 پشتیبانی', callback_data='admx_support'),
            InlineKeyboardButton('⚙️ تنظیمات', callback_data='admx_settings'),
        ],
        [InlineKeyboardButton(
            '📢 مدیریت جوین اجباری', callback_data='admx_forcedjoin'
        )],
        [InlineKeyboardButton('📈 سود فروش جم', callback_data='admx_profit')],
        [
            InlineKeyboardButton('⭐ استودیو ظاهر', callback_data='studio_home'),
            InlineKeyboardButton('👮 لیست و مدیر پریمیوم', callback_data='admx_admins'),
        ],
        [
            InlineKeyboardButton('🔄 بروزرسانی قیمت جم', callback_data='admx_pricesync'),
            InlineKeyboardButton('➕ افزودن ادمین', callback_data='admi_admin'),
        ],
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
            InlineKeyboardButton('سفارش‌ها', callback_data=f'adm_ords_{tg_id}'),
        ],
        [
            InlineKeyboardButton('➕ شارژ کیف پول', callback_data=f'adm_wal_{tg_id}'),
            InlineKeyboardButton('➖ کسر کیف پول', callback_data=f'adm_wdeduct_{tg_id}'),
        ],
        [
            InlineKeyboardButton('🗑 خالی کردن کیف پول', callback_data=f'adm_wempty_{tg_id}'),
            InlineKeyboardButton('✏️ تنظیم دقیق موجودی', callback_data=f'adm_wset_{tg_id}'),
        ],
        [block_btn],
        [InlineKeyboardButton('بازگشت', callback_data='adm_home')],
    ])


def admin_stuck_order_keyboard(order_id, tg_id=''):
    """کیبورد مدیریت سفارش گیرکرده/باز — انجام دستی یا لغو و بازپرداخت."""
    rows = [
        [
            InlineKeyboardButton('✅ انجام شد', callback_data=f'adm_done_{order_id}'),
            InlineKeyboardButton('🗑 لغو + بازپرداخت', callback_data=f'adm_cancel_{order_id}'),
        ],
        [InlineKeyboardButton('🔁 تلاش مجدد تحویل', callback_data=f'adm_retry_{order_id}')],
    ]
    if tg_id:
        rows.append([InlineKeyboardButton('👤 کارت کاربر', callback_data=f'adm_user_{tg_id}')])
    rows.append([InlineKeyboardButton('🔙 بازگشت', callback_data='adm_home')])
    return InlineKeyboardMarkup(rows)


def admin_failed_order_keyboard(order_id, tg_id=''):
    rows = [
        [
            InlineKeyboardButton('✅ انجام شد', callback_data=f'adm_done_{order_id}'),
            InlineKeyboardButton('🗑 لغو + بازپرداخت', callback_data=f'adm_cancel_{order_id}'),
        ],
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
