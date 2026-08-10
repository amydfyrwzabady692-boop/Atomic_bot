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


def freefire_products_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            '🆔 جم با آیدی · تحویل لحظه‌ای', callback_data='gems_by_id'
        )],
        [InlineKeyboardButton(
            '🔐 جم با اطلاعات · هفتگی / ماهانه', callback_data='gems_credentials'
        )],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='home')],
    ])


def credential_products_keyboard(products):
    rows = [
        [InlineKeyboardButton(
            f'{p[1]} • {_fmt(p[4])} تومان', callback_data=f'cred_product_{p[0]}'
        )]
        for p in products
    ]
    rows.append([InlineKeyboardButton('🔙 روش‌های خرید', callback_data='gems')])
    return InlineKeyboardMarkup(rows)


def credential_method_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📧 Gmail / Google', callback_data='cred_method_google')],
        [InlineKeyboardButton('📘 Facebook', callback_data='cred_method_facebook')],
        [InlineKeyboardButton('🟣 VK', callback_data='cred_method_vk')],
        [InlineKeyboardButton('❌ انصراف', callback_data='cred_cancel')],
    ])


def credential_backup_keyboard():
    """قبل از پرداخت: راهنما در متن؛ اگر بلد نیست رد کند. پشتیبانی بعد از پرداخت."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            '🆘 نیاز به راهنمایی — بک‌آپ بلد نیستم',
            callback_data='cred_backup_skip',
        )],
        [InlineKeyboardButton('❌ انصراف و حذف اطلاعات', callback_data='cred_cancel')],
    ])


def credential_post_pay_support_keyboard(order_id, support_username='lookurback'):
    """بعد از پرداخت: تیکت داخل ربات + لینک پیوی پشتیبانی."""
    username = str(support_username or 'lookurback').lstrip('@').strip() or 'lookurback'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f'🆘 تیکت راهنمایی بک‌آپ (سفارش #{order_id})',
            callback_data=f'cred_ticket_{order_id}',
        )],
        [InlineKeyboardButton(
            f'💬 پیوی پشتیبانی (@{username})',
            url=f'https://t.me/{username}',
        )],
    ])


def credential_admin_home_keyboard(counts=None):
    ready = int((counts or {}).get('ready_creds') or 0)
    tickets = int((counts or {}).get('cred_tickets') or 0)
    orders_label = f'🔐 سفارش‌های جم با اطلاعات ({ready})' if ready else '🔐 سفارش‌های جم با اطلاعات'
    tickets_label = f'🎫 تیکت‌های این بخش ({tickets})' if tickets else '🎫 تیکت‌های این بخش'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(orders_label, callback_data='admx_credentials')],
        [InlineKeyboardButton(tickets_label, callback_data='adm_cred_tickets')],
        [InlineKeyboardButton('🔄 بروزرسانی', callback_data='admx_credhub')],
    ])


def credential_2fa_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('✅ فعال است', callback_data='cred_2fa_yes'),
            InlineKeyboardButton('❌ فعال نیست', callback_data='cred_2fa_no'),
        ],
        [InlineKeyboardButton('❌ انصراف', callback_data='cred_cancel')],
    ])


def credential_cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('❌ انصراف و حذف اطلاعات', callback_data='cred_cancel')],
    ])


def credential_confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ تأیید و ساخت سفارش', callback_data='cred_confirm')],
        [InlineKeyboardButton('❌ انصراف و حذف اطلاعات', callback_data='cred_cancel')],
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


def admin_home_keyboard(counts=None):
    """منوی اصلی پنل — گروه‌بندی شفاف با نشانگر موارد نیازمند اقدام."""
    c = counts or {}
    ops_n = int(c.get('ops_alerts') or 0)
    orders_n = int(c.get('orders_action') or 0)
    tickets_n = int(c.get('open_tickets') or 0)
    ops_label = f'🚨 مرکز عملیات ({ops_n})' if ops_n else '🚨 مرکز عملیات'
    orders_label = f'📦 سفارش‌ها ({orders_n})' if orders_n else '📦 سفارش‌ها'
    support_label = f'🎧 پشتیبانی ({tickets_n})' if tickets_n else '🎧 پشتیبانی'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ops_label, callback_data='admx_ops')],
        [
            InlineKeyboardButton(orders_label, callback_data='admx_hub_orders'),
            InlineKeyboardButton('👥 کاربران', callback_data='admx_hub_users'),
        ],
        [
            InlineKeyboardButton('🛍 کاتالوگ فروش', callback_data='admx_shop'),
            InlineKeyboardButton('💳 مالی و درگاه', callback_data='admx_finance'),
        ],
        [InlineKeyboardButton('💱 قیمت‌گذاری و سود', callback_data='admx_pricing')],
        [
            InlineKeyboardButton('📊 گزارش‌ها', callback_data='admx_hub_reports'),
            InlineKeyboardButton(support_label, callback_data='admx_hub_support'),
        ],
        [InlineKeyboardButton('⚙️ تنظیمات سیستم', callback_data='admx_hub_system')],
        [InlineKeyboardButton('🔄 بروزرسانی پنل', callback_data='adm_home')],
    ])


def admin_hub_orders_keyboard(counts=None):
    c = counts or {}
    cred = int(c.get('ready_creds') or 0)
    stuck = int(c.get('stuck') or 0)
    failed = int(c.get('failed_g2') or 0)
    receipts = int(c.get('receipts') or 0)
    cred_label = f'🔐 جم با اطلاعات ({cred})' if cred else '🔐 جم با اطلاعات'
    stuck_label = f'⏳ گیرکرده ({stuck})' if stuck else '⏳ گیرکرده در پردازش'
    failed_label = f'❌ تحویل ناموفق G2B ({failed})' if failed else '❌ تحویل ناموفق G2B'
    receipt_label = f'🧾 رسید کارت‌به‌کارت ({receipts})' if receipts else '🧾 رسیدهای کارت‌به‌کارت'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(cred_label, callback_data='admx_credentials')],
        [InlineKeyboardButton('📂 سفارش‌های باز', callback_data='adm_open')],
        [InlineKeyboardButton(failed_label, callback_data='adm_failed')],
        [InlineKeyboardButton(stuck_label, callback_data='admx_stuck')],
        [InlineKeyboardButton('💰 برگشت به کیف پول', callback_data='admx_refunds')],
        [InlineKeyboardButton(receipt_label, callback_data='admx_receipts')],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def admin_hub_users_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔎 جستجوی کاربر', callback_data='adm_find')],
        [
            InlineKeyboardButton('📋 آخرین کاربران', callback_data='adm_users'),
            InlineKeyboardButton('💰 دارای موجودی', callback_data='adm_users_balance'),
        ],
        [InlineKeyboardButton('📨 پیام همگانی', callback_data='admx_actions')],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def admin_hub_reports_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📈 آمار کلی', callback_data='admx_stats')],
        [InlineKeyboardButton('📅 فروش امروز', callback_data='admx_daily')],
        [
            InlineKeyboardButton('💎 سود جم', callback_data='admx_profit'),
            InlineKeyboardButton('🩺 سلامت مالی', callback_data='admx_health'),
        ],
        [InlineKeyboardButton('❌ پرداخت‌های ناموفق', callback_data='admx_payments_failed')],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def admin_hub_support_keyboard(counts=None):
    tickets = int((counts or {}).get('open_tickets') or 0)
    ticket_label = f'🎫 تیکت‌های باز ({tickets})' if tickets else '🎫 تیکت‌های باز'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ticket_label, callback_data='adm_tickets')],
        [InlineKeyboardButton(
            '🔐 تیکت‌های جم با اطلاعات', callback_data='adm_cred_tickets'
        )],
        [InlineKeyboardButton('🏢 دپارتمان‌ها', callback_data='admx_departments')],
        [InlineKeyboardButton('👤 پشتیبان‌ها و متن‌ها', callback_data='admx_support')],
        [InlineKeyboardButton(
            '🔐 افزودن پشتیبان جم با اطلاعات', callback_data='admi_credentialadmin'
        )],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def admin_hub_system_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🏪 ظاهر و متن فروشگاه', callback_data='admx_settings')],
        [InlineKeyboardButton('📢 جوین اجباری کانال', callback_data='admx_forcedjoin')],
        [InlineKeyboardButton('🔄 سینک قیمت جم از G2B', callback_data='admx_pricesync')],
        [
            InlineKeyboardButton('⭐ استودیو ظاهر', callback_data='studio_home'),
            InlineKeyboardButton('👮 مدیران ربات', callback_data='admx_admins'),
        ],
        [InlineKeyboardButton('➕ افزودن مدیر جدید', callback_data='admi_admin')],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def admin_shop_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💎 بسته‌های جم با آیدی', callback_data='admx_gems')],
        [InlineKeyboardButton('💱 قیمت‌گذاری هفتگی/ماهانه', callback_data='admx_pricing')],
        [InlineKeyboardButton('🎯 پک‌های سنس', callback_data='admx_sense')],
        [
            InlineKeyboardButton('📦 اکانت‌های فروشگاه', callback_data='admx_products'),
            InlineKeyboardButton('🗂 دسته‌بندی‌ها', callback_data='admx_categories'),
        ],
        [
            InlineKeyboardButton('🎁 کد هدیه', callback_data='admx_gift'),
            InlineKeyboardButton('🏷 کد تخفیف', callback_data='admx_discount'),
        ],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def admin_finance_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💱 قیمت‌گذاری و سود', callback_data='admx_pricing')],
        [InlineKeyboardButton('🚦 توقف/فعال فروش', callback_data='admx_toggle_sales')],
        [InlineKeyboardButton('🛡 توقف/فعال پرداخت', callback_data='admx_toggle_payments')],
        [
            InlineKeyboardButton('🟢/🔴 زرین‌پال', callback_data='admx_toggle_zp'),
            InlineKeyboardButton('🟢/🔴 کارت‌به‌کارت', callback_data='admx_toggle_card'),
        ],
        [InlineKeyboardButton('✏️ مرچنت زرین‌پال', callback_data='admi_zpmerchant')],
        [InlineKeyboardButton('✏️ آدرس callback', callback_data='admi_callback')],
        [
            InlineKeyboardButton('✏️ شماره کارت', callback_data='admi_cardnumber'),
            InlineKeyboardButton('✏️ صاحب کارت', callback_data='admi_cardholder'),
        ],
        [InlineKeyboardButton('✏️ نام بانک', callback_data='admi_cardbank')],
        [
            InlineKeyboardButton('🧾 رسیدها', callback_data='admx_receipts'),
            InlineKeyboardButton('📒 گزارش پرداخت', callback_data='admx_payments_all'),
        ],
        [
            InlineKeyboardButton('🩺 سلامت مالی', callback_data='admx_health'),
            InlineKeyboardButton('🧭 لاگ مدیران', callback_data='admx_audit'),
        ],
        [InlineKeyboardButton('📈 گزارش سود جم', callback_data='admx_profit')],
        [InlineKeyboardButton('🔙 منوی اصلی', callback_data='adm_home')],
    ])


def admin_pricing_keyboard():
    """هاب قیمت‌گذاری: بهای دلاری + درصد سود جدا برای آیدی / هفتگی / ماهانه."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💎 سود جم با آیدی', callback_data='admi_gemprofit')],
        [
            InlineKeyboardButton('📅 سود هفتگی', callback_data='admi_credprofit_weekly'),
            InlineKeyboardButton('📆 سود ماهانه', callback_data='admi_credprofit_monthly'),
        ],
        [
            InlineKeyboardButton('💵 بهای دلاری هفتگی', callback_data='admi_credcost_weekly'),
            InlineKeyboardButton('💵 بهای دلاری ماهانه', callback_data='admi_credcost_monthly'),
        ],
        [InlineKeyboardButton('💱 نرخ دستی دلار (پشتیبان)', callback_data='admi_usdrate')],
        [InlineKeyboardButton('🔄 اعمال الان روی قیمت‌های فروش', callback_data='admx_pricesync')],
        [InlineKeyboardButton('📊 نرخ زنده و بهای پک‌ها', callback_data='admx_g2balance')],
        [
            InlineKeyboardButton('🔙 مالی', callback_data='admx_finance'),
            InlineKeyboardButton('🏠 منوی اصلی', callback_data='adm_home'),
        ],
    ])


def admin_user_keyboard(tg_id, is_blocked=False):
    block_btn = (
        InlineKeyboardButton('✅ آنبلاک کاربر', callback_data=f'adm_block_0_{tg_id}')
        if is_blocked else
        InlineKeyboardButton('🚫 بلاک کاربر', callback_data=f'adm_block_1_{tg_id}')
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('💬 پیام به کاربر', callback_data=f'adm_msg_{tg_id}'),
            InlineKeyboardButton('📦 سفارش‌هایش', callback_data=f'adm_ords_{tg_id}'),
        ],
        [
            InlineKeyboardButton('➕ شارژ کیف پول', callback_data=f'adm_wal_{tg_id}'),
            InlineKeyboardButton('➖ کسر از کیف پول', callback_data=f'adm_wdeduct_{tg_id}'),
        ],
        [
            InlineKeyboardButton('🗑 صفر کردن موجودی', callback_data=f'adm_wempty_{tg_id}'),
            InlineKeyboardButton('✏️ تنظیم دقیق موجودی', callback_data=f'adm_wset_{tg_id}'),
        ],
        [block_btn],
        [InlineKeyboardButton('🔙 منوی کاربران', callback_data='admx_hub_users')],
        [InlineKeyboardButton('🏠 منوی اصلی', callback_data='adm_home')],
    ])


def admin_stuck_order_keyboard(order_id, tg_id=''):
    """کیبورد مدیریت سفارش گیرکرده/باز — انجام دستی یا لغو و بازپرداخت."""
    rows = [
        [
            InlineKeyboardButton('✅ انجام‌شده ثبت کن', callback_data=f'adm_done_{order_id}'),
            InlineKeyboardButton('🗑 لغو + ریفاند', callback_data=f'adm_cancel_{order_id}'),
        ],
        [InlineKeyboardButton('🔁 تلاش مجدد تحویل', callback_data=f'adm_retry_{order_id}')],
    ]
    if tg_id:
        rows.append([InlineKeyboardButton('👤 کارت کاربر', callback_data=f'adm_user_{tg_id}')])
    rows.append([InlineKeyboardButton('🔙 سفارش‌ها', callback_data='admx_hub_orders')])
    return InlineKeyboardMarkup(rows)


def admin_failed_order_keyboard(order_id, tg_id=''):
    rows = [
        [
            InlineKeyboardButton('✅ انجام‌شده ثبت کن', callback_data=f'adm_done_{order_id}'),
            InlineKeyboardButton('🗑 لغو + ریفاند', callback_data=f'adm_cancel_{order_id}'),
        ],
        [InlineKeyboardButton('🔁 تلاش مجدد تحویل', callback_data=f'adm_retry_{order_id}')],
    ]
    if tg_id:
        rows.append([InlineKeyboardButton('👤 کارت کاربر', callback_data=f'adm_user_{tg_id}')])
    rows.append([InlineKeyboardButton('🔙 تحویل‌های ناموفق', callback_data='adm_failed')])
    rows.append([InlineKeyboardButton('📦 منوی سفارش‌ها', callback_data='admx_hub_orders')])
    return InlineKeyboardMarkup(rows)


def admin_ticket_keyboard(ticket_id, tg_id=None, *, back_to='admx_hub_support'):
    rows = [
        [
            InlineKeyboardButton('💬 پاسخ', callback_data=f'adm_treply_{ticket_id}'),
            InlineKeyboardButton('✅ بستن تیکت', callback_data=f'adm_tclose_{ticket_id}'),
        ],
    ]
    if tg_id:
        rows.append([InlineKeyboardButton('👤 کارت کاربر', callback_data=f'adm_user_{tg_id}')])
    rows.append([InlineKeyboardButton('🔙 بازگشت', callback_data=back_to)])
    if back_to != 'admx_credhub':
        rows.append([InlineKeyboardButton('🏠 منوی اصلی', callback_data='adm_home')])
    else:
        rows.append([InlineKeyboardButton('🏠 پنل جم با اطلاعات', callback_data='admx_credhub')])
    return InlineKeyboardMarkup(rows)
