"""ظاهر بخش‌های کاربری: متن سفارشی + ایموجی پریمیوم. منطق خرید را عوض نمی‌کند."""
from telegram import MessageEntity
from telegram.ext.filters import MessageFilter

_CACHE = None
BUTTON_TEXT_MAX = 64
MESSAGE_TEXT_MAX = 3500

# دو هاب پنل ادمین: متن‌ها / دکمه‌ها و محصولات
HUBS = {
    't': {
        'title': '📝 متن‌ها و توضیحات',
        'hint': 'عنوان صفحه، توضیح بخش‌ها و متن‌هایی که کاربر می‌بیند.',
        'categories': {
            'welcome': {
                'title': '🏠 خوش‌آمد و راهنما',
                'items': (
                    ('t.welcome', 'متن خوش‌آمد /start', True),
                    ('t.help', 'متن راهنما /help', True),
                    ('t.home', 'متن بازگشت به منو', True),
                ),
            },
            'ff': {
                'title': '🎮 محصولات فری‌فایر',
                'items': (
                    ('t.ff.hdr', 'توضیح انتخاب روش خرید', True),
                ),
            },
            'gems': {
                'title': '💎 جم با آیدی',
                'items': (
                    ('t.gems.hdr', 'عنوان لیست بسته‌ها', True),
                ),
            },
            'creds': {
                'title': '🔐 جم با اطلاعات',
                'items': (
                    ('t.creds.hdr', 'توضیح صفحه جم با اطلاعات', True),
                ),
            },
            'wallet': {
                'title': '💰 کیف پول',
                'items': (
                    ('t.wallet.hdr', 'متن صفحه کیف پول — از {balance} برای موجودی', True),
                ),
            },
            'account': {
                'title': '👤 حساب و سفارش‌ها',
                'items': (
                    ('t.account.hdr', 'عنوان حساب من', False),
                    ('t.orders.hdr', 'عنوان سفارش‌های من', False),
                    ('t.orders.empty', 'متن وقتی سفارشی نیست', True),
                ),
            },
            'store': {
                'title': '🛍 فروشگاه اکانت',
                'items': (
                    ('t.store.pick', 'متن انتخاب دسته‌بندی', True),
                ),
            },
            'sense': {
                'title': '🎯 پک سنس',
                'items': (
                    ('t.sense.hdr', 'عنوان انتخاب پلتفرم', True),
                    ('t.sense.pc', 'عنوان پک سنس PC', False),
                    ('t.sense.mob', 'عنوان پک سنس موبایل', False),
                ),
            },
            'support': {
                'title': '🎧 پشتیبانی',
                'items': (
                    ('t.support', 'متن ورود به پشتیبانی', True),
                ),
            },
            'giftcards': {
                'title': '🎁 گیفت کارت',
                'items': (
                    ('t.gc.hdr', 'متن صفحه خرید گیفت کارت', True),
                ),
            },
            'stars': {
                'title': '⭐ استارز تلگرام',
                'items': (
                    ('t.stars.hdr', 'عنوان لیست استارز', True),
                ),
            },
        },
    },
    'b': {
        'title': '🔘 منوها، دکمه‌ها و محصولات',
        'hint': 'متن دکمه و ایموجی پریمیوم جلوی هر گزینه. شناسه/قیمت/پرداخت عوض نمی‌شود.',
        'categories': {
            'menu': {
                'title': '📱 منوی پایین',
                'items': (
                    ('b.menu.ff', 'محصولات فری‌فایر', False),
                    ('b.menu.wal', 'کیف پول', False),
                    ('b.menu.ord', 'سفارش‌های من', False),
                    ('b.menu.acc', 'حساب من', False),
                    ('b.menu.st', 'فروشگاه اکانت', False),
                    ('b.menu.se', 'پک سنس', False),
                    ('b.menu.stars', 'خرید استارز', False),
                    ('b.menu.gc', 'خرید گیفت کارت', False),
                    ('b.menu.su', 'پشتیبانی', False),
                ),
            },
            'ff': {
                'title': '🎮 دکمه‌های محصولات فری‌فایر',
                'items': (
                    ('b.gems.id', 'جم با آیدی', False),
                    ('b.gems.cr', 'جم با اطلاعات', False),
                    ('b.nav.home', 'منوی اصلی (بازگشت)', False),
                ),
            },
            'gems': {
                'title': '💎 دکمه‌های بسته‌های جم با آیدی',
                'dynamic': 'gem',
                'items': (),
            },
            'creds': {
                'title': '🔐 دکمه‌های جم با اطلاعات',
                'dynamic': 'cred',
                'items': (),
            },
            'gemact': {
                'title': '💎 دکمه‌های خرید جم با آیدی',
                'items': (
                    ('b.gem.buy', 'خرید این بسته', False),
                    ('b.gem.ok', 'تایید و ادامه پرداخت', False),
                    ('b.gem.no', 'انصراف جم', False),
                ),
            },
            'wallet': {
                'title': '💰 دکمه‌های کیف پول',
                'items': (
                    ('b.wal.50', '۵۰ هزار', False),
                    ('b.wal.100', '۱۰۰ هزار', False),
                    ('b.wal.200', '۲۰۰ هزار', False),
                    ('b.wal.500', '۵۰۰ هزار', False),
                    ('b.wal.custom', 'مبلغ دلخواه', False),
                ),
            },
            'pay': {
                'title': '💳 دکمه‌های پرداخت',
                'items': (
                    ('b.pay.zp', 'زرین‌پال', False),
                    ('b.pay.card', 'کارت‌به‌کارت', False),
                    ('b.pay.wal', 'کیف پول (پرداخت سفارش)', False),
                ),
            },
            'sense': {
                'title': '🎯 دکمه‌های پک سنس',
                'dynamic': 'sense',
                'items': (
                    ('b.se.pc', 'پلتفرم PC', False),
                    ('b.se.mob', 'پلتفرم موبایل', False),
                ),
            },
            'giftcards': {
                'title': '🎁 دکمه‌های گیفت کارت',
                'items': (
                    ('b.gc.gplay_us', 'گوگل‌پلی آمریکا', False),
                    ('b.gc.itunes_us', 'آیتونز آمریکا', False),
                    ('b.gc.itunes_tr', 'آیتونز ترکیه', False),
                    ('b.gc.gplay_tr', 'گوگل‌پلی ترکیه', False),
                ),
            },
            'stars': {
                'title': '⭐ دکمه‌های استارز',
                'dynamic': 'stars',
                'items': (
                    ('b.stars.buy', 'خرید این بسته استارز', False),
                    ('b.stars.ok', 'تایید استارز و پرداخت', False),
                    ('b.stars.no', 'انصراف استارز', False),
                ),
            },
            'store': {
                'title': '🛍 دکمه‌های فروشگاه اکانت',
                'dynamic': 'store',
                'items': (),
            },
        },
    },
}

DEFAULTS = {
    't.welcome': (
        "✨ به اتومیک شاپ خوش اومدی! ✨\n\n"
        "اینجا جاییه که سرعت، امنیت و قیمت مناسب کنار هم جمع شدن "
        "تا خرید راحت‌تری داشته باشی 🚀\n\n"
        "💎 خرید جم و محصولات بازی\n"
        "🎯 پک‌های حرفه‌ای سنسیویتی موبایل و PC\n"
        "💳 پرداخت امن با درگاه یا کارت‌به‌کارت\n"
        "🎁 کدهای هدیه و تخفیف‌های ویژه\n"
        "🧑‍💻 پشتیبانی مستقیم و پیگیری سفارش\n\n"
        "تمام سفارش‌ها و پرداخت‌های تو از داخل ربات قابل مشاهده و پیگیری هستند.\n\n"
        "👇 برای شروع، یکی از گزینه‌های منوی پایین را انتخاب کن.\n\n"
        "⚛️ Atomic Shop"
    ),
    't.help': (
        "📋 *راهنمای Atomic Bot*\n"
        "━━━━━━━━━━━━━━━\n"
        "🎮 *محصولات فری‌فایر* — خرید با آیدی (فعال)\n"
        "⭐ *استارز تلگرام* — شارژ مستقیم با آیدی\n"
        "🎁 *گیفت کارت* — گوگل‌پلی و آیتونز آمریکا/ترکیه\n"
        "💰 *کیف پول* — شارژ و موجودی (فعال)\n"
        "📦 *سفارش‌های من* — وضعیت سفارش‌ها\n"
        "👤 *حساب من* — پروفایل و موجودی\n"
        "🎧 *پشتیبانی* — پیام به ادمین\n\n"
        "سایر بخش‌ها در حال بروزرسانی هستند.\n\n"
        "⚠️ پرداخت زرین‌پال: لینک را *کپی* کن → VPN خاموش → در مرورگر باز کن.\n"
        "🆔 دستور `/myid` آیدی عددی تلگرام تو را نشان می‌دهد."
    ),
    't.home': 'از منوی پایین یک گزینه انتخاب کن 👇',
    't.ff.hdr': (
        '🎮 *محصولات فری‌فایر*\n'
        '━━━━━━━━━━━━━━━\n'
        'روش خرید را انتخاب کن:\n\n'
        '🆔 *جم با آیدی*\n'
        '⚡ تحویل لحظه‌ای · قیمت پایین\n'
        'فقط آیدی بازی را می‌فرستی و جم خودکار واریز می‌شود.\n\n'
        '🔐 *جم با اطلاعات*\n'
        '📅 عضویت هفتگی و ماهانه + بویاه پس گیفتی\n'
        'هفتگی/ماهانه با اطلاعات ورود اکانت؛ گیفتی فقط با آیدی بازی.'
    ),
    't.gems.hdr': (
        "🆔 *جم فری‌فایر با آیدی*\n"
        "بسته موردنظرت را انتخاب کن — صفحه {page} از {total} 👇"
    ),
    't.creds.hdr': (
        '🔐 *جم با اطلاعات اکانت*\n'
        '━━━━━━━━━━━━━━━\n'
        'عضویت *هفتگی* یا *ماهانه*، یا *بویاه پس گیفتی* را انتخاب کن.\n'
        'هفتگی/ماهانه: تعداد ← روش ورود ← شناسه ← رمز ← بک‌آپ.\n'
        'گیفتی: فقط آیدی بازی؛ بعد از پرداخت به پیوی ادمین شماره سفارش را بفرست.\n\n'
        'راهنمای گرفتن بک‌آپ برای Gmail / Facebook / VK داخل چت می‌آید.\n'
        '🔒 بعد از پرداخت، دسترسی به آیدی پشتیبان باز می‌شود.'
    ),
    't.wallet.hdr': (
        "💰 *کیف پول Atomic*\n"
        "━━━━━━━━━━━━━━━\n"
        "موجودی فعلی: *{balance} تومان*\n\n"
        "مبلغ شارژ را انتخاب کن؛ بعد روش پرداخت (درگاه یا کارت‌به‌کارت) را می‌گیری."
    ),
    't.account.hdr': '✦ *حساب من*',
    't.orders.hdr': '📦 *سفارش‌های من*',
    't.orders.empty': '📦 *سفارش‌های من*\n\nهنوز سفارشی ثبت نکردی!',
    't.store.pick': 'دسته‌بندی را انتخاب کن:',
    't.sense.hdr': "✦ *پک سنس*\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄\nپلتفرم را انتخاب کن:",
    't.sense.pc': '✦ *پک سنس — PC*',
    't.sense.mob': '✦ *پک سنس — موبایل*',
    't.support': (
        "🎧 *پشتیبانی Atomic*\n"
        "━━━━━━━━━━━━━━━\n"
        "پیامت را همین‌جا بفرست (متن).\n"
        "ادمین در تلگرام می‌بیند و جواب می‌دهد.\n\n"
        "برای انصراف /cancel"
    ),
    't.gc.hdr': (
        "🎁 *خرید گیفت کارت*\n"
        "━━━━━━━━━━━━━━━\n"
        "کد بعد از پرداخت به‌صورت آنی در همین چت ارسال می‌شود.\n"
        "قیمت‌ها به تومان است و از کاتالوگ ذخیره‌شده خوانده می‌شود."
    ),
    't.stars.hdr': (
        "⭐ *خرید استارز تلگرام*\n"
        "بسته را انتخاب کن — صفحه {page} از {total} 👇\n"
        "بعد از انتخاب، آیدی اکانتی که می‌خوای استارز بزنی روش را می‌فرستی."
    ),
    'b.menu.ff': '🎮 محصولات فری‌فایر',
    'b.menu.wal': '💰 کیف پول',
    'b.menu.ord': '📦 سفارش‌های من',
    'b.menu.acc': '👤 حساب من',
    'b.menu.st': '🛍 فروشگاه اکانت',
    'b.menu.se': '🎯 پک سنس',
    'b.menu.stars': '⭐ خرید استارز',
    'b.menu.gc': '🎁 خرید گیفت کارت',
    'b.menu.su': '🎧 پشتیبانی',
    'b.gc.gplay_us': '🇺🇸 گوگل‌پلی آمریکا',
    'b.gc.itunes_us': '🇺🇸 آیتونز آمریکا',
    'b.gc.itunes_tr': '🇹🇷 آیتونز ترکیه',
    'b.gc.gplay_tr': '🇹🇷 گوگل‌پلی ترکیه',
    'b.gems.id': '🆔 جم با آیدی · تحویل لحظه‌ای',
    'b.gems.cr': '🔐 جم با اطلاعات · هفتگی / ماهانه',
    'b.nav.home': '🔙 منوی اصلی',
    'b.gem.buy': '✅ خرید این بسته',
    'b.gem.ok': '✅ تایید و ادامه پرداخت',
    'b.gem.no': '✖️ انصراف',
    'b.stars.buy': '✅ خرید این استارز',
    'b.stars.ok': '✅ تایید و ادامه پرداخت',
    'b.stars.no': '✖️ انصراف',
    'b.wal.50': '۵۰٬۰۰۰ ت',
    'b.wal.100': '۱۰۰٬۰۰۰ ت',
    'b.wal.200': '۲۰۰٬۰۰۰ ت',
    'b.wal.500': '۵۰۰٬۰۰۰ ت',
    'b.wal.custom': '✏️ مبلغ دلخواه',
    'b.pay.zp': '💳 زرین‌پال',
    'b.pay.card': '🏧 کارت‌به‌کارت',
    'b.pay.wal': '💰 کیف پول',
    'b.se.pc': '🖥 PC',
    'b.se.mob': '📱 موبایل',
}

MENU_KEYS = {
    'b.menu.ff': 'ff',
    'b.menu.wal': 'wallet',
    'b.menu.ord': 'orders',
    'b.menu.acc': 'account',
    'b.menu.st': 'store',
    'b.menu.se': 'sense',
    'b.menu.stars': 'stars',
    'b.menu.gc': 'giftcards',
    'b.menu.su': 'support',
}

_LEGACY_MENU = {
    '💎 جم فری‌فایر': 'ff',
    '🛒 سبد خرید': 'cart',
}


class _Safe(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def invalidate_cache():
    global _CACHE
    _CACHE = None


def _rows():
    global _CACHE
    if _CACHE is None:
        try:
            from db import list_appearance_rows
            _CACHE = list_appearance_rows() or {}
        except Exception:
            _CACHE = {}
    return _CACHE


def get(key):
    return _rows().get(str(key) or '') or {}


def utf16_len(text):
    return len((text or '').encode('utf-16-le')) // 2


def utf16_slice(text, offset, length):
    encoded = (text or '').encode('utf-16-le')
    start = max(0, int(offset) * 2)
    end = start + max(0, int(length) * 2)
    return encoded[start:end].decode('utf-16-le')


def extract_custom_emoji(message):
    """اولین ایموجی پریمیوم پیام ادمین را برمی‌گرداند."""
    if message is None:
        return None
    text = message.text or message.caption or ''
    entities = list(message.entities or []) + list(message.caption_entities or [])
    for ent in entities:
        kind = getattr(ent, 'type', None)
        if str(kind) in {'custom_emoji', 'MessageEntityType.CUSTOM_EMOJI'}:
            emoji_id = str(getattr(ent, 'custom_emoji_id', '') or '')
            if not emoji_id:
                continue
            char = utf16_slice(text, ent.offset, ent.length) or '⭐'
            return {'emoji_id': emoji_id, 'emoji_char': char}
    return None


def user_label(key, default=None):
    row = get(key)
    text = (row.get('text') if row else None)
    if text is None or str(text).strip() == '':
        text = default if default is not None else DEFAULTS.get(key, '')
    return str(text)


def user_emoji(key):
    row = get(key) or {}
    return str(row.get('emoji_id') or '')


def user_emoji_char(key):
    row = get(key) or {}
    return str(row.get('emoji_char') or '')


def icon_for(*keys):
    """اولین ایموجی پریمیوم ثبت‌شده بین کلیدها؛ برای دکمه محصول و بخش والد."""
    for key in keys:
        emoji_id = user_emoji(key)
        if emoji_id:
            return emoji_id
    return ''


def normalize_emoji_char(text):
    return (text or '').replace('\ufe0f', '').strip()


# نوع یونیکد مرتبط با هر بخش؛ از پکیج پریمیوم همان نوع برداشته می‌شود.
_EMOJI_TYPES = {
    'b.menu.ff': ('🎮', '💎', '🔥'),
    'b.menu.wal': ('💰', '💵', '💳'),
    'b.menu.ord': ('📦', '📋'),
    'b.menu.acc': ('👑', '👤', '🪪'),
    'b.menu.st': ('🛍', '🛒', '🏪'),
    'b.menu.se': ('🎯', '🎮'),
    'b.menu.stars': ('⭐', '🌟', '👑'),
    'b.menu.gc': ('🎁', '🎀'),
    'b.menu.su': ('🎧', '💬', '🆘'),
    't.welcome': ('✨', '👑', '👋', '⭐'),
    't.help': ('📋', 'ℹ️', '❓'),
    't.home': ('🏠', '👇', '⭐'),
    't.ff.hdr': ('🎮', '💎', '🔥'),
    't.gems.hdr': ('💎', '🎮'),
    't.creds.hdr': ('🔐', '💎'),
    't.wallet.hdr': ('💰', '💳'),
    't.account.hdr': ('👑', '👤', '🪪'),
    't.orders.hdr': ('📦', '📋'),
    't.orders.empty': ('📦', '📋'),
    't.store.pick': ('🛍', '🛒'),
    't.sense.hdr': ('🎯', '🎮'),
    't.sense.pc': ('🖥', '🎯'),
    't.sense.mob': ('📱', '🎯'),
    't.support': ('🎧', '💬'),
    't.gc.hdr': ('🎁', '🎀'),
    't.stars.hdr': ('⭐', '🌟', '👑'),
    'b.gems.id': ('🆔', '💎', '🎮'),
    'b.gems.cr': ('🔐', '💎'),
    'b.nav.home': ('🔙', '🏠'),
    'b.gem.buy': ('✅', '💎'),
    'b.gem.ok': ('✅', '💎'),
    'b.wal.50': ('💰', '💵'),
    'b.wal.100': ('💰', '💵'),
    'b.wal.200': ('💰', '💵'),
    'b.wal.500': ('💰', '💵'),
    'b.wal.custom': ('✏️', '💰'),
    'b.pay.zp': ('💳', '💰'),
    'b.pay.card': ('🏧', '💳', '💰'),
    'b.pay.wal': ('💰', '💳'),
    'b.se.pc': ('🖥', '🎯'),
    'b.se.mob': ('📱', '🎯'),
    'b.gc.gplay_us': ('🇺🇸', '🎁'),
    'b.gc.itunes_us': ('🇺🇸', '🎁'),
    'b.gc.itunes_tr': ('🇹🇷', '🎁'),
    'b.gc.gplay_tr': ('🇹🇷', '🎁'),
    'b.stars.buy': ('✅', '⭐'),
    'b.stars.ok': ('✅', '⭐'),
}

_EMOJI_PREFIX_TYPES = (
    ('st.', ('👑', '⭐', '🌟')),
    ('g.', ('👑', '💎', '🎮')),
    ('c.', ('🔐', '💎', '👑')),
    ('s.', ('🎯', '👑', '🎮')),
    ('sc.', ('🛍', '📁', '📦', '👑')),
    ('sp.', ('📦', '🛍', '👑')),
)


def wanted_emoji_chars(key):
    key = str(key or '')
    chars = _EMOJI_TYPES.get(key)
    if chars:
        return chars
    for prefix, prefix_chars in _EMOJI_PREFIX_TYPES:
        if key.startswith(prefix) and key[len(prefix):].isdigit():
            return prefix_chars
    return ()


def pack_from_appearance_rows(rows):
    pack = {}
    for row in (rows or {}).values():
        emoji_id = str((row or {}).get('emoji_id') or '').strip()
        char = str((row or {}).get('emoji_char') or '')
        if emoji_id and char:
            pack[normalize_emoji_char(char)] = (emoji_id, char)
    return pack


def index_custom_emoji_stickers(stickers, pack=None):
    pack = {} if pack is None else pack
    for sticker in stickers or []:
        emoji_id = str((sticker or {}).get('custom_emoji_id') or '').strip()
        char = str((sticker or {}).get('emoji') or '')
        if emoji_id and char:
            pack[normalize_emoji_char(char)] = (emoji_id, char)
    return pack


def pick_pack_emoji(pack, wanted_chars, fallback_id='', fallback_char='⭐'):
    pack = pack or {}
    for char in wanted_chars or ():
        hit = pack.get(normalize_emoji_char(char))
        if hit:
            return hit
    if fallback_id:
        return str(fallback_id), str(fallback_char or '⭐') or '⭐'
    return '', ''


def is_long_item(key):
    for hub in HUBS.values():
        for cat in hub['categories'].values():
            for item_key, _title, long_text in cat.get('items') or ():
                if item_key == key:
                    return bool(long_text)
    return key.startswith('t.')


def max_len_for(key):
    return MESSAGE_TEXT_MAX if is_long_item(key) else BUTTON_TEXT_MAX


def _safe_format(text, **fmt):
    if not fmt:
        return text
    try:
        return str(text).format_map(_Safe({k: str(v) for k, v in fmt.items()}))
    except Exception:
        return str(text)


def message_kwargs(key, default=None, parse_mode='Markdown', **fmt):
    """kwargs مناسب reply_text / edit_message_text بدون دست زدن به منطق."""
    text = _safe_format(user_label(key, default), **fmt)
    return with_emoji(key, text, parse_mode=parse_mode)


def with_emoji(key, text, parse_mode='Markdown'):
    out = {'text': text}
    emoji_id = user_emoji(key)
    if emoji_id:
        prefix = user_emoji_char(key) or '⭐'
        if not str(text).startswith(prefix):
            text = f'{prefix} {text}'
        out['text'] = text
        out['entities'] = [
            MessageEntity(
                type='custom_emoji',
                offset=0,
                length=utf16_len(prefix),
                custom_emoji_id=emoji_id,
            )
        ]
    elif parse_mode:
        out['parse_mode'] = parse_mode
    return out


def menu_action(text):
    """متن منوی پایین را به اکشن پایدار نگاشت می‌کند تا منطق ربات نشکند."""
    raw = (text or '').strip()
    if raw in _LEGACY_MENU:
        return _LEGACY_MENU[raw]
    for key, action in MENU_KEYS.items():
        if raw == DEFAULTS.get(key) or raw == user_label(key, DEFAULTS.get(key)):
            return action
    return None


def all_menu_labels():
    labels = set(_LEGACY_MENU)
    for key in MENU_KEYS:
        labels.add(DEFAULTS[key])
        labels.add(user_label(key, DEFAULTS[key]))
    return labels


def find_item_meta(key):
    for hub_id, hub in HUBS.items():
        for cat_id, cat in hub['categories'].items():
            for item_key, title, long_text in cat.get('items') or ():
                if item_key == key:
                    return {
                        'hub': hub_id,
                        'category': cat_id,
                        'title': title,
                        'long': long_text,
                        'dynamic': False,
                    }
    parsed = parse_dynamic_key(key)
    if parsed:
        return {
            'hub': 'b',
            'category': parsed['category'],
            'title': parsed['title'],
            'long': False,
            'dynamic': True,
        }
    return None


def parse_dynamic_key(key):
    key = str(key or '')
    if key.startswith('g.') and key[2:].isdigit():
        return {'category': 'gems', 'kind': 'gem', 'pk': int(key[2:]), 'title': f'بسته جم #{key[2:]}'}
    if key.startswith('c.') and key[2:].isdigit():
        return {'category': 'creds', 'kind': 'cred', 'pk': int(key[2:]), 'title': f'جم با اطلاعات #{key[2:]}'}
    if key.startswith('st.') and key[3:].isdigit():
        return {'category': 'stars', 'kind': 'star', 'pk': int(key[3:]), 'title': f'استارز #{key[3:]}'}
    if key.startswith('s.') and key[2:].isdigit():
        return {'category': 'sense', 'kind': 'sense', 'pk': int(key[2:]), 'title': f'پک سنس #{key[2:]}'}
    if key.startswith('sc.') and key[3:].isdigit():
        return {'category': 'store', 'kind': 'storecat', 'pk': int(key[3:]), 'title': f'دسته فروشگاه #{key[3:]}'}
    if key.startswith('sp.') and key[3:].isdigit():
        return {'category': 'store', 'kind': 'storeprod', 'pk': int(key[3:]), 'title': f'محصول فروشگاه #{key[3:]}'}
    return None


def valid_item_key(key):
    return find_item_meta(key) is not None


def dynamic_items(category_id):
    """آیتم‌های زنده محصولات؛ فقط برچسب ظاهر، نه قیمت یا موجودی."""
    items = []
    try:
        if category_id == 'gems':
            from db import get_gems_by_id
            for g in get_gems_by_id() or []:
                items.append((f'g.{g[0]}', str(g[1] or f'بسته #{g[0]}'), False))
        elif category_id == 'creds':
            from db import get_gems_by_credentials
            for g in get_gems_by_credentials() or []:
                items.append((f'c.{g[0]}', str(g[1] or f'بسته #{g[0]}'), False))
        elif category_id == 'stars':
            from db import list_star_packages
            for p in list_star_packages() or []:
                items.append((f'st.{p["id"]}', str(p.get('title') or f'استارز #{p["id"]}'), False))
        elif category_id == 'sense':
            from db import list_sense_packages
            for p in list_sense_packages(active_only=False) or []:
                items.append((f's.{p[0]}', str(p[1] or f'پک #{p[0]}'), False))
        elif category_id == 'store':
            from db import simple_list
            for row in simple_list('ProductCategories', ['Id', 'Title']) or []:
                items.append((f'sc.{row[0]}', f'📁 {row[1]}', False))
            for row in simple_list('StoreProducts', ['Id', 'Title']) or []:
                items.append((f'sp.{row[0]}', f'📦 {row[1]}', False))
    except Exception:
        return items
    return items


def category_items(hub_id, category_id):
    cat = ((HUBS.get(hub_id) or {}).get('categories') or {}).get(category_id) or {}
    items = list(cat.get('items') or ())
    dyn = cat.get('dynamic')
    if dyn:
        items.extend(dynamic_items(category_id))
    return items


def default_for(key):
    parsed = parse_dynamic_key(key)
    if parsed:
        for item_key, title, _long in dynamic_items(parsed['category']):
            if item_key == key:
                return title
        return parsed['title']
    return DEFAULTS.get(key, '')


class MenuActionFilter(MessageFilter):
    """منوی پایین را با متن سفارشی هم تشخیص می‌دهد."""

    def __init__(self, action):
        super().__init__(name=f'MenuAction({action})')
        self.action = action

    def filter(self, message):
        return menu_action(getattr(message, 'text', None)) == self.action
