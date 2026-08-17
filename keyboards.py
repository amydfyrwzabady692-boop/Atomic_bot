import button_style  # noqa: F401 — دکمه‌های رنگی primary/success/danger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

import appearance


def _menu_btn(text, style=None, icon_custom_emoji_id=None):
    kwargs = {}
    extra = {}
    if icon_custom_emoji_id:
        extra['icon_custom_emoji_id'] = str(icon_custom_emoji_id)
    if style:
        try:
            return KeyboardButton(
                text, style=style, icon_custom_emoji_id=icon_custom_emoji_id
            )
        except TypeError:
            extra['style'] = style
            kwargs['api_kwargs'] = extra
            return KeyboardButton(text, **kwargs)
    if extra:
        kwargs['api_kwargs'] = extra
        try:
            return KeyboardButton(text, icon_custom_emoji_id=icon_custom_emoji_id)
        except TypeError:
            return KeyboardButton(text, **kwargs)
    return KeyboardButton(text)


def _kbtn(key, default, style=None):
    return _menu_btn(
        appearance.user_label(key, default),
        style,
        appearance.user_emoji(key) or None,
    )


def _inline_btn(text, callback_data, icon_custom_emoji_id=None):
    """دکمه اینلاین؛ ایموجی پریمیوم را بدون شکستن PTB قدیمی پاس می‌دهد."""
    icon = str(icon_custom_emoji_id or '') or None
    try:
        if icon:
            return InlineKeyboardButton(
                text, callback_data=callback_data, icon_custom_emoji_id=icon,
            )
        return InlineKeyboardButton(text, callback_data=callback_data)
    except TypeError:
        kwargs = {'callback_data': callback_data}
        if icon:
            kwargs['api_kwargs'] = {'icon_custom_emoji_id': icon}
        return InlineKeyboardButton(text, **kwargs)


def _ibtn(key, default, callback_data):
    return _inline_btn(
        appearance.user_label(key, default),
        callback_data,
        appearance.user_emoji(key),
    )

GEM_PRODUCTS_PER_PAGE = 8


def _fmt(n):
    return f"{n:,}"


def main_menu():
    return ReplyKeyboardMarkup(
        [
            [_kbtn('b.menu.ff', '🎮 محصولات فری‌فایر', 'primary'),
             _kbtn('b.menu.wal', '💰 کیف پول', 'success')],
            [_kbtn('b.menu.ord', '📦 سفارش‌های من', 'primary'),
             _kbtn('b.menu.acc', '👤 حساب من', 'primary')],
            [_kbtn('b.menu.st', '🛍 فروشگاه اکانت', 'success'),
             _kbtn('b.menu.se', '🎯 پک سنس', 'primary')],
            [_kbtn('b.menu.su', '🎧 پشتیبانی', 'danger')],
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
        [_ibtn('b.gems.id', '🆔 جم با آیدی · تحویل لحظه‌ای', 'gems_by_id')],
        [_ibtn('b.gems.cr', '🔐 جم با اطلاعات · هفتگی / ماهانه', 'gems_credentials')],
        [_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')],
    ])


def credential_products_keyboard(products):
    rows = []
    for p in products:
        key = f'c.{p[0]}'
        title = appearance.user_label(key, p[1])
        rows.append([_inline_btn(
            f'{title} • {_fmt(p[4])} تومان',
            f'cred_product_{p[0]}',
            appearance.user_emoji(key),
        )])
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


def credential_post_pay_support_keyboard(order_id, support_username='lookurback', support_url=None):
    """بعد از پرداخت: تیکت داخل ربات + لینک پیوی پشتیبانی."""
    rows = [[InlineKeyboardButton(
        f'🆘 تیکت راهنمایی بک‌آپ (سفارش #{order_id})',
        callback_data=f'cred_ticket_{order_id}',
    )]]
    url = str(support_url or '').strip()
    label = str(support_username or 'پشتیبانی').strip() or 'پشتیبانی'
    if not url:
        username = label.lstrip('@').strip() or 'lookurback'
        if username.isdigit():
            url = f'https://t.me/user?id={username}'
            label = username
        else:
            url = f'https://t.me/{username}'
            label = f'@{username}'
    elif not label.startswith('@') and not label.isdigit():
        label = f'@{label.lstrip("@")}'
    rows.append([InlineKeyboardButton(
        f'💬 پیوی پشتیبانی ({label})',
        url=url,
    )])
    return InlineKeyboardMarkup(rows)


def credential_admin_home_keyboard(counts=None):
    c = counts or {}
    ready = int(c.get('ready_creds') or 0)
    site_ready = int(c.get('site_ready_creds') or 0)
    tickets = int(c.get('cred_tickets') or 0)
    bot_label = f'🔐 جم با اطلاعات ربات ({ready})' if ready else '🔐 جم با اطلاعات ربات'
    site_label = (
        f'🌐 جم با اطلاعات سایت ({site_ready})' if site_ready else '🌐 جم با اطلاعات سایت'
    )
    tickets_label = f'🎫 تیکت‌های این بخش ({tickets})' if tickets else '🎫 تیکت‌های این بخش'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(bot_label, callback_data='admx_credentials')],
        [InlineKeyboardButton(site_label, callback_data='admx_sitecreds')],
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
        [_ibtn('b.se.pc', '🖥 PC', 'sens_pc')],
        [_ibtn('b.se.mob', '📱 موبایل', 'sens_mobile')],
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
            _inline_btn(
                f"{appearance.user_label(f's.{key}', title)} — {price:,} ت",
                f"sens_buy_{key}",
                appearance.user_emoji(f's.{key}'),
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
        title = appearance.user_label(f'g.{g[0]}', g[1])
        icon = appearance.user_emoji(f'g.{g[0]}') or None
        label = f"{auto} {title}  •  {_fmt(g[4])} تومان"
        if sold_out and not g[8]:
            label = f"❌ ناموجود — {title}"
            buttons.append([_inline_btn(label, 'noop', icon)])
        else:
            buttons.append([_inline_btn(label, f'gem_{g[0]}', icon)])
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
        [_ibtn('b.gem.buy', '✅ خرید این بسته', f'gbuy_{gem_id}')],
        [InlineKeyboardButton(
            '🔙 بازگشت به لیست', callback_data=f'gems_page_{max(1, int(page))}'
        )],
    ])


def gem_cancel_keyboard():
    return InlineKeyboardMarkup([
        [_ibtn('b.gem.no', '✖️ انصراف', 'gem_cancel')],
    ])


def gem_confirm_keyboard():
    return InlineKeyboardMarkup([
        [_ibtn('b.gem.ok', '✅ تایید و ادامه پرداخت', 'gem_confirm')],
        [InlineKeyboardButton('✏️ اصلاح آیدی', callback_data='gem_reedit')],
        [_ibtn('b.gem.no', '✖️ انصراف', 'gem_cancel')],
    ])


def pay_method_keyboard(order_id, can_wallet=True, wallet_balance=0, remaining=None):
    """همیشه دکمه کیف پول را نشان بده (حتی با موجودی صفر) تا کاربر بداند این روش هست."""
    bal = int(wallet_balance or 0)
    rem = int(remaining) if remaining is not None else None
    rows = [
        [_ibtn('b.pay.zp', '💳 زرین‌پال', f'pay_zp_{order_id}')],
        [_ibtn('b.pay.card', '🏧 کارت‌به‌کارت', f'pay_card_{order_id}')],
    ]
    if can_wallet and (rem is None or rem > 0):
        if bal <= 0:
            label = f'{appearance.user_label("b.pay.wal", "💰 کیف پول")} (موجودی صفر)'
        elif rem is not None and bal >= rem:
            label = f'{appearance.user_label("b.pay.wal", "💰 کیف پول")} — پرداخت کامل'
        else:
            label = f'{appearance.user_label("b.pay.wal", "💰 کیف پول")} ({bal:,} ت)'
        rows.append([_inline_btn(
            label,
            f'pay_wallet_{order_id}',
            appearance.user_emoji('b.pay.wal'),
        )])
    rows.append([InlineKeyboardButton('انصراف', callback_data=f'cancel_order_{order_id}')])
    return InlineKeyboardMarkup(rows)


def wallet_charge_method_keyboard(amount):
    return InlineKeyboardMarkup([
        [_ibtn('b.pay.zp', '💳 درگاه زرین‌پال', f'wpay_zp_{amount}')],
        [_ibtn('b.pay.card', '🏧 کارت‌به‌کارت', f'wpay_card_{amount}')],
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


def site_card_keyboard(payment_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                '✅ تأیید رسید', callback_data=f'site_review_ok_{payment_id}'
            ),
            InlineKeyboardButton(
                '❌ رد رسید', callback_data=f'site_review_no_{payment_id}'
            ),
        ],
    ])


def site_card_confirm_keyboard(payment_id, action):
    if action == 'ok':
        label, callback = '⚠️ تأیید نهایی رسید سایت', f'site_ok_{payment_id}'
    else:
        label, callback = '⚠️ رد نهایی رسید سایت', f'site_no_{payment_id}'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=callback)],
        [InlineKeyboardButton('🔙 بازگشت', callback_data=f'site_review_back_{payment_id}')],
    ])


def wallet_keyboard():
    return InlineKeyboardMarkup([
        [
            _ibtn('b.wal.50', '۵۰٬۰۰۰ ت', 'wchg_50000'),
            _ibtn('b.wal.100', '۱۰۰٬۰۰۰ ت', 'wchg_100000'),
        ],
        [
            _ibtn('b.wal.200', '۲۰۰٬۰۰۰ ت', 'wchg_200000'),
            _ibtn('b.wal.500', '۵۰۰٬۰۰۰ ت', 'wchg_500000'),
        ],
        [_ibtn('b.wal.custom', '✏️ مبلغ دلخواه', 'wchg_custom')],
        [_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')],
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
        [InlineKeyboardButton('✨ ظاهر', callback_data='ap_home')],
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
    site_cred = int(c.get('site_ready_creds') or 0)
    stuck = int(c.get('stuck') or 0)
    failed = int(c.get('failed_g2') or 0)
    receipts = int(c.get('receipts') or 0)
    site_receipts = int(c.get('site_receipts') or 0)
    cred_label = f'🔐 جم با اطلاعات ربات ({cred})' if cred else '🔐 جم با اطلاعات ربات'
    site_cred_label = (
        f'🌐 جم با اطلاعات سایت ({site_cred})' if site_cred else '🌐 جم با اطلاعات سایت'
    )
    stuck_label = f'⏳ گیرکرده ({stuck})' if stuck else '⏳ گیرکرده در پردازش'
    failed_label = f'❌ تحویل ناموفق G2B ({failed})' if failed else '❌ تحویل ناموفق G2B'
    receipt_label = f'🧾 رسیدهای ربات ({receipts})' if receipts else '🧾 رسیدهای ربات'
    site_receipt_label = (
        f'🌐 رسیدهای سایت ({site_receipts})' if site_receipts else '🌐 رسیدهای سایت'
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(cred_label, callback_data='admx_credentials')],
        [InlineKeyboardButton(site_cred_label, callback_data='admx_sitecreds')],
        [InlineKeyboardButton('📂 سفارش‌های باز', callback_data='adm_open')],
        [InlineKeyboardButton(failed_label, callback_data='adm_failed')],
        [InlineKeyboardButton(stuck_label, callback_data='admx_stuck')],
        [InlineKeyboardButton('💰 برگشت به کیف پول', callback_data='admx_refunds')],
        [InlineKeyboardButton(receipt_label, callback_data='admx_receipts')],
        [InlineKeyboardButton(site_receipt_label, callback_data='admx_sitereceipts')],
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
        [InlineKeyboardButton('✨ ظاهر متن و دکمه‌ها', callback_data='ap_home')],
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
            InlineKeyboardButton('🧾 رسید ربات', callback_data='admx_receipts'),
            InlineKeyboardButton('🌐 رسید سایت', callback_data='admx_sitereceipts'),
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
