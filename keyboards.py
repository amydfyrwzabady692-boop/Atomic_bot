import button_style  # noqa: F401 — دکمه‌های رنگی primary/success/danger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

import appearance


from buttons import _LEADING_EMOJI


def _menu_btn(text, style=None, icon_custom_emoji_id=None):
    kwargs = {}
    extra = {}
    if icon_custom_emoji_id:
        t_str = str(text or '')
        while _LEADING_EMOJI.match(t_str):
            stripped = _LEADING_EMOJI.sub('', t_str, count=1)
            if stripped.strip():
                t_str = stripped
            else:
                break
        text = t_str
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
    from game import button_emoji
    icon = button_emoji.get_button_icon(key) or appearance.user_emoji(key) or None
    return _menu_btn(
        appearance.user_label(key, default),
        style,
        icon,
    )


def _inline_btn(text, callback_data, icon_custom_emoji_id=None):
    """دکمه اینلاین؛ ایموجی پریمیوم را بدون شکستن PTB قدیمی پاس می‌دهد و از ایموجی تکراری جلوگیری می‌کند."""
    icon = str(icon_custom_emoji_id or '') or None
    if icon:
        t_str = str(text or '')
        while _LEADING_EMOJI.match(t_str):
            stripped = _LEADING_EMOJI.sub('', t_str, count=1)
            if stripped.strip():
                t_str = stripped
            else:
                break
        text = t_str
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
    from game import button_emoji
    icon = button_emoji.get_button_icon(key) or appearance.user_emoji(key) or None
    return _inline_btn(
        appearance.user_label(key, default),
        callback_data,
        icon,
    )

GEM_PRODUCTS_PER_PAGE = 8


def _fmt(n):
    return f"{n:,}"


def _referral_menu_visible():
    """دکمه «دعوت دوستان» فقط وقتی مدیر بخش را روشن کرده باشد."""
    try:
        import referral_db
        return referral_db.menu_visible()
    except Exception:
        return False


def main_menu():
    rows = [
        [_kbtn('b.menu.ff', '🎮 محصولات فری‌فایر', 'primary'),
         _kbtn('b.menu.wal', '💰 کیف پول', 'success')],
        # همهٔ بازی‌های دیگر (پابجی، کالاف، …) پشت یک دکمه، نه یک دکمه برای هر بازی.
        [_kbtn('b.menu.games', '🕹 شارژ بازی‌ها | پابجی، کالاف و…', 'success')],
        [_kbtn('b.menu.ord', '📦 سفارش‌های من', 'primary'),
         _kbtn('b.menu.acc', '👤 حساب من', 'primary')],
        [_kbtn('b.menu.st', '🛍 فروشگاه اکانت', 'success'),
         _kbtn('b.menu.se', '🎯 پک سنس', 'primary')],
        [_kbtn('b.menu.stars', '⭐ خرید استارز', 'primary'),
         _kbtn('b.menu.gc', '🎁 خرید گیفت کارت', 'success')],
        [_kbtn('b.menu.su', '🎧 پشتیبانی', 'danger'),
         _kbtn('b.menu.prem', '💎 تلگرام پرمیوم', 'primary')],
    ]
    if _referral_menu_visible():
        rows.insert(5, [_kbtn('b.menu.ref', appearance.DEFAULTS['b.menu.ref'], 'success')])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        input_field_placeholder='از منوی پایین انتخاب کن…',
    )


def giftcard_menu_keyboard(catalog=None):
    rows = []
    brands = (catalog or {}).get('brands') or {}
    brand_icons = {
        'gplay_us': '6024100028195279285',  # 3D Google Play Logo
        'gplay_tr': '6024100028195279285',  # 3D Google Play Logo
        'itunes_us': '6023902876311490783', # 3D Apple App Store Logo
        'itunes_tr': '6023902876311490783', # 3D Apple App Store Logo
    }
    for brand in (
        'gplay_us', 'itunes_us', 'itunes_tr', 'gplay_tr',
    ):
        title = appearance.user_label(
            f'b.gc.{brand}',
            {
                'gplay_us': '🇺🇸 گوگل‌پلی آمریکا',
                'itunes_us': '🇺🇸 آیتونز آمریکا',
                'itunes_tr': '🇹🇷 آیتونز ترکیه',
                'gplay_tr': '🇹🇷 گوگل‌پلی ترکیه',
            }[brand],
        )
        items = brands.get(brand) or []
        live = sum(1 for item in items if item.get('can_buy'))
        suffix = f' · {live} موجود' if items else ''
        icon = appearance.icon_for(f'b.gc.{brand}') or brand_icons.get(brand, '4958699241137505132')
        rows.append([_inline_btn(
            f'{title}{suffix}',
            f'gc_b_{brand}',
            icon,
        )])
    rows.append([_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')])
    return InlineKeyboardMarkup(rows)


def giftcard_list_keyboard(brand, items):
    rows = []
    default_brand_icon = '6024100028195279285' if 'gplay' in str(brand) else '6023902876311490783'
    for item in items or []:
        face = str(item.get('face_label') or item.get('title') or '')
        price = int(item.get('sale_toman') or 0)
        curr = str(item.get('currency') or '')
        item_icon = appearance.icon_for(f'gc.p.{item.get("id")}')
        if not item_icon:
            if 'دلار' in face or '$' in face or curr.upper() == 'USD':
                item_icon = '4956601935592424315'  # 3D Emerald Green Dollar Sign
            elif 'لیر' in face or curr.upper() == 'TRY':
                item_icon = '5888625992196431593'  # 3D Cyan Gift Card
            else:
                item_icon = default_brand_icon
        if item.get('can_buy') and price > 0:
            rows.append([_inline_btn(
                f'{face}  •  {_fmt(price)} تومان',
                f'gc_p_{item["id"]}',
                item_icon,
            )])
        else:
            rows.append([_inline_btn(
                f'❌ ناموجود — {face}',
                'noop',
                '4994791839895651680',
            )])
    rows.append([_inline_btn('🔙 دسته‌ها', 'gc_home', '5307731102771719870')])
    return InlineKeyboardMarkup(rows)


def giftcard_buy_keyboard(product_id, brand):
    return InlineKeyboardMarkup([
        [_inline_btn(
            '✅ خرید این گیفت‌کارت', f'gc_buy_{int(product_id)}',
            '5307875864644432289',
        )],
        [InlineKeyboardButton('🔙 بازگشت به لیست', callback_data=f'gc_b_{brand}')],
    ])


def stars_list_keyboard(packages, page=1, per_page=8):
    packages = list(packages or [])
    total_pages = max(1, (len(packages) + per_page - 1) // per_page)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * per_page
    buttons = []
    for pkg in packages[start:start + per_page]:
        key = f'st.{pkg["id"]}'
        title = appearance.user_label(key, pkg['title'])
        icon = appearance.icon_for(key)
        if not icon:
            stars_amt = int(pkg.get('stars') or 0)
            if stars_amt >= 50000:
                icon = '5425107576809349359'
            elif stars_amt >= 10000:
                icon = '5427116221344539269'
            elif stars_amt >= 1500:
                icon = '5424786953205733725'
            elif stars_amt >= 750:
                icon = '5424680163138888970'
            elif stars_amt >= 250:
                icon = '5424978650481057780'
            elif stars_amt >= 150:
                icon = '5424925590455085174'
            else:
                icon = '5425040837312538501'
        price = int(pkg.get('price') or 0)
        if pkg.get('available') and price > 0:
            buttons.append([_inline_btn(
                f'{title}  •  {_fmt(price)} تومان',
                f'st_{pkg["id"]}',
                icon,
            )])
        else:
            buttons.append([_inline_btn(
                f'❌ ناموجود — {title}',
                'noop',
                icon,
            )])
    if total_pages > 1:
        nav = [
            InlineKeyboardButton(
                f'• {number} •' if number == page else str(number),
                callback_data='noop' if number == page else f'st_page_{number}',
            )
            for number in range(1, total_pages + 1)
        ]
        buttons.append(nav)
    buttons.append([_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')])
    return InlineKeyboardMarkup(buttons)


def stars_detail_keyboard(package_id, page=1, back=None):
    if back:
        # پرمیوم: همان مسیرِ خرید، بازگشت به فهرستِ پرمیوم
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ خرید این بسته', callback_data=f'st_buy_{int(package_id)}')],
            [InlineKeyboardButton('🔙 بازگشت به لیست', callback_data=back)],
        ])
    return InlineKeyboardMarkup([
        [_ibtn('b.stars.buy', '✅ خرید این استارز', f'st_buy_{int(package_id)}')],
        [InlineKeyboardButton(
            '🔙 بازگشت به لیست', callback_data=f'st_page_{max(1, int(page))}'
        )],
    ])


def premium_list_keyboard(packages):
    """تلگرام پرمیوم: یک دکمه برای هر مدت، با قیمت."""
    rows = []
    for pkg in packages or []:
        label = f"{pkg['title']} — {int(pkg['price']):,} ت"
        if not pkg.get('available'):
            label += ' (ناموجود)'
        rows.append([InlineKeyboardButton(label, callback_data=f"st_{int(pkg['id'])}")])
    rows.append([_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')])
    return InlineKeyboardMarkup(rows)


def stars_uid_keyboard(own_username=''):
    rows = []
    if own_username:
        rows.append([_inline_btn(
            f'آیدی خودم (@{own_username})',
            'st_self',
            appearance.icon_for('b.menu.stars'),
        )])
    rows.append([_ibtn('b.stars.no', '✖️ انصراف', 'st_cancel')])
    return InlineKeyboardMarkup(rows)


def stars_cancel_keyboard():
    return InlineKeyboardMarkup([
        [_ibtn('b.stars.no', '✖️ انصراف', 'st_cancel')],
        [_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')],
    ])


def stars_confirm_keyboard():
    return InlineKeyboardMarkup([
        [_ibtn('b.stars.ok', '✅ تایید و ادامه پرداخت', 'st_confirm')],
        [InlineKeyboardButton('✏️ اصلاح آیدی', callback_data='st_reedit')],
        [_ibtn('b.stars.no', '✖️ انصراف', 'st_cancel')],
    ])


def updating_keyboard(back='home'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('بازگشت', callback_data=back)],
    ])


def freefire_products_keyboard():
    return InlineKeyboardMarkup([
        [_ibtn('b.gems.id', '🆔 جم با آیدی · تحویل لحظه‌ای', 'gems_by_id')],
        [_ibtn('b.gems.cr', '🔐 جم با اطلاعات · هفتگی / ماهانه / گیفتی', 'gems_credentials')],
        [_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')],
    ])


def credential_products_keyboard(products):
    rows = []
    has_levelup = any(len(p) > 6 and p[6] == 'levelup' for p in products)
    if has_levelup:
        rows.append([InlineKeyboardButton(
            '🛒 انتخاب و خرید چند پک با هم (۱۰٪ تخفیف)',
            callback_data='cred_multi_select',
        )])

    for p in products:
        key = f'c.{p[0]}'
        title = appearance.user_label(key, p[1])
        rows.append([_inline_btn(
            f'{title} • {_fmt(p[4])} تومان',
            f'cred_product_{p[0]}',
            appearance.icon_for(key, f'g.{p[0]}', 'b.gems.cr'),
        )])
    rows.append([InlineKeyboardButton('🔙 روش‌های خرید', callback_data='gems')])
    return InlineKeyboardMarkup(rows)


def credential_multi_select_keyboard(levelup_pkgs, selected_ids, total_price=0):
    rows = []
    selected_set = set(selected_ids or [])
    for p in levelup_pkgs:
        pid = p[0]
        title = appearance.user_label(f'c.{pid}', p[1])
        check = '✅' if pid in selected_set else '⬜️'
        btn_text = f'{check} {title} • {_fmt(p[4])} تومان'
        rows.append([InlineKeyboardButton(btn_text, callback_data=f'cred_toggle_{pid}')])

    all_pids = {p[0] for p in levelup_pkgs}
    all_selected = bool(all_pids and all_pids.issubset(selected_set))
    toggle_all_text = '❌ لغو انتخاب همه' if all_selected else '✨ انتخاب همه پک‌ها'
    rows.append([InlineKeyboardButton(toggle_all_text, callback_data='cred_toggle_all')])

    if selected_ids:
        rows.append([InlineKeyboardButton(
            f'✅ ادامه و ثبت اطلاعات ({_fmt(total_price)} تومان)',
            callback_data='cbuy_multi',
        )])
    else:
        rows.append([InlineKeyboardButton('👆 لطفاً حداقل یک پک را انتخاب کنید', callback_data='cred_multi_noop')])

    rows.append([InlineKeyboardButton('🔙 بازگشت به لیست محصولات', callback_data='gems_credentials')])
    return InlineKeyboardMarkup(rows)


def credential_method_keyboard():
    icon = appearance.icon_for('b.gems.cr', 'b.menu.ff')
    return InlineKeyboardMarkup([
        [_inline_btn('📧 Gmail / Google', 'cred_method_google', icon)],
        [_inline_btn('📘 Facebook', 'cred_method_facebook', icon)],
        [_inline_btn('🟣 VK', 'cred_method_vk', icon)],
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


def credential_post_pay_support_keyboard(order_id, support_username='lookurback', support_url=None, mode='credentials'):
    """بعد از پرداخت: تیکت داخل ربات + لینک پیوی پشتیبانی."""
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
    if str(mode or '') == 'gift':
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f'💬 پیوی ادمین — سفارش #{order_id} را بفرست',
                url=url,
            )],
            [InlineKeyboardButton(
                f'🆘 تیکت داخل ربات (سفارش #{order_id})',
                callback_data=f'cred_ticket_{order_id}',
            )],
        ])
    rows = [[InlineKeyboardButton(
        f'🆘 تیکت راهنمایی بک‌آپ (سفارش #{order_id})',
        callback_data=f'cred_ticket_{order_id}',
    )]]
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
        [_inline_btn(
            '✅ تأیید و ساخت سفارش', 'cred_confirm',
            appearance.icon_for('b.gem.ok', 'b.gems.cr'),
        )],
        [InlineKeyboardButton('❌ انصراف و حذف اطلاعات', callback_data='cred_cancel')],
    ])


def sens_platform_keyboard():
    return InlineKeyboardMarkup([
        [_ibtn('b.se.pc', '🖥 PC', 'sens_pc')],
        [_ibtn('b.se.mob', '📱 موبایل', 'sens_mobile')],
        [_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')],
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
                appearance.icon_for(f's.{key}', 'b.menu.se'),
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
        icon = appearance.icon_for(f'g.{g[0]}', 'b.gems.id', 'b.menu.ff') or None
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
    buttons.append([_ibtn('b.nav.home', '🔙 منوی اصلی', 'home')])
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


def _is_zp_enabled():
    try:
        from db import get_bool_setting
        return get_bool_setting('zarinpal_enabled', False)
    except Exception:
        return False


def pay_method_keyboard(order_id, can_wallet=True, wallet_balance=0, remaining=None):
    """همیشه دکمه کیف پول را نشان بده (حتی با موجودی صفر) تا کاربر بداند این روش هست."""
    bal = int(wallet_balance or 0)
    rem = int(remaining) if remaining is not None else None
    rows = []
    if _is_zp_enabled():
        rows.append([_ibtn('b.pay.zp', '💳 زرین‌پال', f'pay_zp_{order_id}')])
    rows.append([_ibtn('b.pay.card', '🏧 کارت‌به‌کارت', f'pay_card_{order_id}')])
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
            appearance.icon_for('b.pay.wal', 'b.menu.wal'),
        )])
    rows.append([InlineKeyboardButton('انصراف', callback_data=f'cancel_order_{order_id}')])
    return InlineKeyboardMarkup(rows)


def wallet_charge_method_keyboard(amount):
    rows = []
    if _is_zp_enabled():
        rows.append([_ibtn('b.pay.zp', '💳 درگاه زرین‌پال', f'wpay_zp_{amount}')])
    rows.append([_ibtn('b.pay.card', '🏧 کارت‌به‌کارت', f'wpay_card_{amount}')])
    rows.append([InlineKeyboardButton('بازگشت', callback_data='wallet')])
    return InlineKeyboardMarkup(rows)


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
        [InlineKeyboardButton('بازگشت به کیف پول', callback_data='wallet')],
    ])
    return InlineKeyboardMarkup(rows)


def support_cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('❌ انصراف', callback_data='support_cancel')],
    ])


def _count_label(label, value):
    value = int(value or 0)
    return f'{label} ({value})' if value else label


def _on_off(label, enabled, on='روشن', off='خاموش'):
    """دکمهٔ روشن/خاموش که وضعیتِ فعلی را نشان می‌دهد؛ زدنش وضعیت را برعکس می‌کند."""
    return f'{"🟢" if enabled else "🔴"} {label}: {on if enabled else off}'


def admin_home_keyboard(counts=None):
    """منوی اصلی پنل: ده بخشِ ثابت. عددِ داخل پرانتز یعنی کارِ منتظر در همان بخش.

    هر گزینهٔ پنل فقط در یک بخش است (به‌جز کارهای فوری که میان‌بُرِ کارهای منتظر است).
    """
    c = counts or {}
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_count_label('⚡ کارهای فوری', c.get('ops_alerts')),
                              callback_data='admx_ops')],
        [
            InlineKeyboardButton(_count_label('📦 سفارش‌ها', c.get('orders_action')),
                                 callback_data='admx_hub_orders'),
            InlineKeyboardButton(_count_label('🧾 پرداخت و رسید', c.get('receipts')),
                                 callback_data='admx_hub_pay'),
        ],
        [
            InlineKeyboardButton(_count_label('🪪 احراز هویت', c.get('identity')),
                                 callback_data='idva_home'),
            InlineKeyboardButton('👥 کاربران', callback_data='admx_hub_users'),
        ],
        [
            InlineKeyboardButton('🛍 محصولات و قیمت', callback_data='admx_shop'),
            InlineKeyboardButton(_count_label('🎧 پشتیبانی', c.get('open_tickets')),
                                 callback_data='admx_hub_support'),
        ],
        [
            InlineKeyboardButton('📣 تبلیغ و کانال‌ها', callback_data='admx_hub_promo'),
            InlineKeyboardButton('📊 گزارش‌ها', callback_data='admx_hub_reports'),
        ],
        [InlineKeyboardButton('⚙️ تنظیمات', callback_data='admx_hub_system')],
        [InlineKeyboardButton('🔄 بروزرسانی پنل', callback_data='adm_home')],
    ])


def _home_row():
    return [InlineKeyboardButton('🏠 منوی اصلی', callback_data='adm_home')]


def admin_hub_orders_keyboard(counts=None):
    c = counts or {}
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('🔎 استعلام سفارش', callback_data='admi_ordersearch'),
            InlineKeyboardButton('📋 همه سفارش‌ها', callback_data='admx_allorders'),
        ],
        [
            InlineKeyboardButton('📂 سفارش‌های باز', callback_data='adm_open'),
            InlineKeyboardButton(_count_label('⏳ گیرکرده', c.get('stuck')),
                                 callback_data='admx_stuck'),
        ],
        [InlineKeyboardButton(_count_label('❌ تحویل ناموفق G2B', c.get('failed_g2')),
                              callback_data='adm_failed')],
        [
            InlineKeyboardButton(_count_label('🔐 جم با اطلاعات ربات', c.get('ready_creds')),
                                 callback_data='admx_credentials'),
            InlineKeyboardButton(_count_label('🌐 جم با اطلاعات سایت', c.get('site_ready_creds')),
                                 callback_data='admx_sitecreds'),
        ],
        [InlineKeyboardButton(_count_label('⭐ استارز دستی', c.get('manual_stars')),
                              callback_data='admx_manual_stars')],
        _home_row(),
    ])


def admin_hub_pay_keyboard(counts=None, state=None):
    """رسیدها، برگشت پول، گزارش پرداخت، و روشن/خاموشِ درگاه‌ها با وضعیتِ فعلی."""
    c = counts or {}
    st = state or {}
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_count_label('🧾 رسیدهای ربات', c.get('receipts')),
                                 callback_data='admx_receipts'),
            InlineKeyboardButton(_count_label('🌐 رسیدهای سایت', c.get('site_receipts')),
                                 callback_data='admx_sitereceipts'),
        ],
        [InlineKeyboardButton(_count_label('💰 برگشت به کیف پول', c.get('refunds')),
                              callback_data='admx_refunds')],
        [
            InlineKeyboardButton('📒 گزارش پرداخت', callback_data='admx_payments_all'),
            InlineKeyboardButton('❌ پرداخت‌های ناموفق', callback_data='admx_payments_failed'),
        ],
        [
            InlineKeyboardButton(_on_off('زرین‌پال', st.get('zp', True)),
                                 callback_data='admx_toggle_zp'),
            InlineKeyboardButton(_on_off('کارت‌به‌کارت', st.get('card', True)),
                                 callback_data='admx_toggle_card'),
        ],
        [
            InlineKeyboardButton(_on_off('فروش', st.get('sales', True), 'فعال', 'متوقف'),
                                 callback_data='admx_toggle_sales'),
            InlineKeyboardButton(_on_off('پرداخت جدید', st.get('payments', True), 'فعال', 'متوقف'),
                                 callback_data='admx_toggle_payments'),
        ],
        [InlineKeyboardButton('💳 کارت مقصد و تنظیمات درگاه', callback_data='admx_finance')],
        _home_row(),
    ])


def admin_hub_users_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔎 جستجوی کاربر', callback_data='adm_find')],
        [
            InlineKeyboardButton('📋 آخرین کاربران', callback_data='adm_users'),
            InlineKeyboardButton('💰 دارای موجودی', callback_data='adm_users_balance'),
        ],
        [InlineKeyboardButton('📨 پیام همگانی و شارژ همگانی', callback_data='admx_actions')],
        [InlineKeyboardButton('🎁 رفرال و برترین دعوت‌کننده‌ها', callback_data='radm_home')],
        _home_row(),
    ])


def admin_hub_promo_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('👥 گروه‌ها و تبلیغ ۱۲ ساعته', callback_data='grp_home')],
        [InlineKeyboardButton('🆕 رویداد جدید بازی · انتشار در چنل', callback_data='gev_home')],
        [InlineKeyboardButton('📢 جوین اجباری کانال', callback_data='admx_forcedjoin')],
        [InlineKeyboardButton('🎁 دعوت دوستان و مسابقه', callback_data='radm_home')],
        [InlineKeyboardButton('📨 پیام همگانی', callback_data='admx_actions')],
        _home_row(),
    ])


def admin_hub_reports_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('📈 آمار کلی', callback_data='admx_stats'),
            InlineKeyboardButton('📅 فروش امروز', callback_data='admx_daily'),
        ],
        [
            InlineKeyboardButton('💎 سود جم', callback_data='admx_profit'),
            InlineKeyboardButton('🩺 سلامت مالی', callback_data='admx_health'),
        ],
        [
            InlineKeyboardButton('📒 گزارش پرداخت', callback_data='admx_payments_all'),
            InlineKeyboardButton('❌ پرداخت‌های ناموفق', callback_data='admx_payments_failed'),
        ],
        [InlineKeyboardButton('🧭 لاگ کارهای مدیران', callback_data='admx_audit')],
        _home_row(),
    ])


def admin_hub_support_keyboard(counts=None):
    tickets = int((counts or {}).get('open_tickets') or 0)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_count_label('🎫 تیکت‌های باز', tickets), callback_data='adm_tickets')],
        [InlineKeyboardButton('🔐 تیکت‌های جم با اطلاعات', callback_data='adm_cred_tickets')],
        [
            InlineKeyboardButton('🏢 دپارتمان‌ها', callback_data='admx_departments'),
            InlineKeyboardButton('👤 آیدی‌ها و متن پشتیبانی', callback_data='admx_support'),
        ],
        [InlineKeyboardButton('🔐 افزودن پشتیبان جم با اطلاعات', callback_data='admi_credentialadmin')],
        _home_row(),
    ])


def admin_hub_system_keyboard(state=None):
    st = state or {}
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🏪 نام فروشگاه، خوش‌آمد و متن‌ها', callback_data='admx_settings')],
        [
            InlineKeyboardButton('✨ ظاهر متن و دکمه‌ها', callback_data='ap_home'),
            InlineKeyboardButton('🎨 تم ایموجی پریمیوم', callback_data='th_home:text'),
        ],
        [InlineKeyboardButton('⭐ استودیو ظاهر', callback_data='studio_home')],
        [InlineKeyboardButton('🪪 سقف احراز هویت', callback_data='admx_idvsettings')],
        [InlineKeyboardButton('👮 مدیران ربات', callback_data='admx_admins')],
        [InlineKeyboardButton(_on_off('اعلان خودکار به مدیر', st.get('alerts', True)),
                              callback_data='admx_toggle_alerts')],
        [InlineKeyboardButton('🔄 سینک قیمت جم از G2B', callback_data='admx_pricesync')],
        _home_row(),
    ])


def admin_identity_settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✏️ سقف عکس مدارک', callback_data='admi_idvlimit')],
        [InlineKeyboardButton('✏️ سقف ویدیو', callback_data='admi_idvvideo')],
        [InlineKeyboardButton('🪪 صف احراز هویت', callback_data='idva_home')],
        [InlineKeyboardButton('🔙 تنظیمات', callback_data='admx_hub_system')],
    ])


def admin_shop_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('💎 جم با آیدی', callback_data='admx_gems'),
            InlineKeyboardButton('🎯 پک‌های سنس', callback_data='admx_sense'),
        ],
        [
            InlineKeyboardButton('📦 اکانت‌های فروشگاه', callback_data='admx_products'),
            InlineKeyboardButton('🗂 دسته‌بندی‌ها', callback_data='admx_categories'),
        ],
        [
            InlineKeyboardButton('🎁 گیفت‌کارت G2B', callback_data='admx_giftcards'),
            InlineKeyboardButton('⭐ استارز و پریمیوم', callback_data='admx_stars'),
        ],
        [
            InlineKeyboardButton('🎁 کد هدیه', callback_data='admx_gift'),
            InlineKeyboardButton('🏷 کد تخفیف', callback_data='admx_discount'),
        ],
        [InlineKeyboardButton('💱 قیمت‌گذاری و سود', callback_data='admx_pricing')],
        _home_row(),
    ])


def admin_finance_keyboard():
    """کارت مقصد و تنظیمات درگاه؛ روشن/خاموش‌ها در «پرداخت و رسید» است."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('✏️ شماره کارت', callback_data='admi_cardnumber'),
            InlineKeyboardButton('✏️ صاحب کارت', callback_data='admi_cardholder'),
        ],
        [InlineKeyboardButton('✏️ نام بانک', callback_data='admi_cardbank')],
        [
            InlineKeyboardButton('✏️ مرچنت زرین‌پال', callback_data='admi_zpmerchant'),
            InlineKeyboardButton('✏️ آدرس callback', callback_data='admi_callback'),
        ],
        [InlineKeyboardButton('🔙 پرداخت و رسید', callback_data='admx_hub_pay')],
        _home_row(),
    ])


def admin_pricing_keyboard():
    """هاب قیمت‌گذاری: بهای دلاری + درصد سود جدا برای آیدی / هفتگی / ماهانه."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💎 سود جم با آیدی', callback_data='admi_gemprofit')],
        [InlineKeyboardButton('⭐ سود استارز', callback_data='admi_starprofit')],
        [InlineKeyboardButton('🎁 سود گیفت‌کارت', callback_data='admi_giftprofit')],
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
            InlineKeyboardButton('🔙 محصولات و قیمت', callback_data='admx_shop'),
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
