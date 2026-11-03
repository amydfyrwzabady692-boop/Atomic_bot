"""گروه‌هایی که ربات عضوشان است، و تبلیغ اتومیک شاپ هر ۱۲ ساعت در هر کدام.

هر نوبت پیام تبلیغِ قبلیِ همان گروه پاک می‌شود و پیام تازه جایش می‌نشیند، تا
گروه پر از تبلیغ تکراری نشود. متن پیش‌فرض محصولات را معرفی می‌کند و زیرش
دکمه‌های خرید از ربات و از سایت AtomicShop.ir است؛ مدیر با ‎/adtext‎ متن را
عوض می‌کند و در پنل برای هر گروه تبلیغ را روشن/خاموش می‌کند.
"""
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_LOG = logging.getLogger(__name__)

SITE = 'https://atomicshop.ir'
UTM = 'utm_source=telegram&utm_medium=group&utm_campaign=bot_ad'
AD_TEXT_KEY = 'group_ad_text'
AD_HOURS_KEY = 'group_ad_hours'
DEFAULT_HOURS = 12

DEFAULT_AD = (
    '🔥 <b>اتومیک شاپ | AtomicShop.ir</b>\n'
    'خرید سریع و مطمئن، فقط با آیدی — بدون رمز اکانت ✅\n\n'
    '💎 <b>جم فری‌فایر با آیدی</b> — تحویل آنی روی سرور خاورمیانه\n'
    '🎮 <b>یوسی پابجی، سی‌پی کالاف دیوتی</b> و شارژ ده‌ها بازی دیگر\n'
    '⭐ <b>استارز و 💎 پرمیوم تلگرام</b> — فقط با یوزرنیم\n'
    '🎁 <b>گیفت کارت</b> اپل، گوگل‌پلی، استیم و پلی‌استیشن با کد آنی\n\n'
    '⚡ تحویل خودکار در چند دقیقه  |  💳 پرداخت ریالی  |  🎧 پشتیبانی واقعی\n'
    '🛡 اگر سفارشی انجام نشود، کل مبلغ به کیف پولت برمی‌گردد.\n\n'
    '👇 همین حالا از ربات یا سایت خرید کن'
)


def _db():
    import db
    return db


def interval_hours():
    try:
        value = int(str(_db().get_setting(AD_HOURS_KEY, str(DEFAULT_HOURS)) or DEFAULT_HOURS))
    except ValueError:
        value = DEFAULT_HOURS
    return max(1, min(168, value))


def ad_text():
    custom = str(_db().get_setting(AD_TEXT_KEY, '') or '').strip()
    return custom or DEFAULT_AD


def set_ad_text(text):
    _db().set_setting(AD_TEXT_KEY, str(text or '').strip())


def ad_keyboard(bot_username):
    bot = f'https://t.me/{bot_username}?start=grp' if bot_username else f'{SITE}/?{UTM}'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🤖 خرید از ربات', url=bot),
         InlineKeyboardButton('🌐 خرید از AtomicShop.ir', url=f'{SITE}/?{UTM}')],
        [InlineKeyboardButton('💎 جم فری‌فایر', url=f'{SITE}/store/gems/?{UTM}'),
         InlineKeyboardButton('🎮 یوسی و شارژ بازی‌ها', url=f'{SITE}/store/games/?{UTM}')],
        [InlineKeyboardButton('⭐ استارز و پرمیوم تلگرام', url=f'{SITE}/store/telegram-stars/?{UTM}'),
         InlineKeyboardButton('🎁 گیفت کارت', url=f'{SITE}/store/gift-cards/?{UTM}')],
    ])


# ── دیتابیس ──────────────────────────────────────────────────────────────

def remember_group(chat_id, title='', username='', chat_type='', is_member=True):
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "BotGroups" ("ChatId","Title","Username","ChatType","IsMember","UpdatedAt") '
            'VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT ("ChatId") DO UPDATE SET '
            '"Title"=COALESCE(NULLIF(EXCLUDED."Title",\'\'),"BotGroups"."Title"),'
            '"Username"=EXCLUDED."Username","ChatType"=EXCLUDED."ChatType",'
            '"IsMember"=EXCLUDED."IsMember","UpdatedAt"=now()',
            (int(chat_id), str(title or '')[:255], str(username or '')[:64],
             str(chat_type or '')[:20], bool(is_member)))
        conn.commit()


def migrate_group(old_chat_id, new_chat_id):
    """گروه معمولی که سوپرگروه شد شناسهٔ تازه می‌گیرد."""
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute('UPDATE "BotGroups" SET "ChatId"=%s,"LastAdMessageId"=NULL,"UpdatedAt"=now() '
                    'WHERE "ChatId"=%s AND NOT EXISTS (SELECT 1 FROM "BotGroups" WHERE "ChatId"=%s)',
                    (int(new_chat_id), int(old_chat_id), int(new_chat_id)))
        conn.commit()


def _row(row):
    return {'chat_id': int(row[0]), 'title': row[1] or '', 'username': row[2] or '',
            'is_member': bool(row[3]), 'ads': bool(row[4]), 'last_message_id': row[5],
            'last_ad_at': row[6], 'last_error': row[7] or ''}


_COLUMNS = ('"ChatId","Title","Username","IsMember","AdsEnabled","LastAdMessageId",'
            '"LastAdAt","LastError"')


def list_groups():
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT {_COLUMNS} FROM "BotGroups" ORDER BY "IsMember" DESC, "Title"')
        return [_row(row) for row in cur.fetchall()]


def get_group(chat_id):
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT {_COLUMNS} FROM "BotGroups" WHERE "ChatId"=%s', (int(chat_id),))
        row = cur.fetchone()
    return _row(row) if row else None


def toggle_ads(chat_id):
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute('UPDATE "BotGroups" SET "AdsEnabled"=NOT "AdsEnabled","UpdatedAt"=now() '
                    'WHERE "ChatId"=%s RETURNING "AdsEnabled"', (int(chat_id),))
        row = cur.fetchone()
        conn.commit()
    return bool(row[0]) if row else None


def due_groups(hours=None):
    hours = hours or interval_hours()
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_COLUMNS} FROM "BotGroups" WHERE "IsMember" AND "AdsEnabled" '
            'AND ("LastAdAt" IS NULL OR "LastAdAt" <= now() - make_interval(hours => %s)) '
            'ORDER BY "LastAdAt" NULLS FIRST', (int(hours),))
        return [_row(row) for row in cur.fetchall()]


def record_ad(chat_id, message_id):
    with _db().get_conn() as conn, conn.cursor() as cur:
        cur.execute('UPDATE "BotGroups" SET "LastAdMessageId"=%s,"LastAdAt"=now(),"LastError"=\'\','
                    '"UpdatedAt"=now() WHERE "ChatId"=%s', (int(message_id), int(chat_id)))
        conn.commit()


def record_error(chat_id, error, *, left=False):
    with _db().get_conn() as conn, conn.cursor() as cur:
        # خطا هم نوبت را جلو می‌برد تا هر ده دقیقه دوباره امتحان نشود.
        cur.execute('UPDATE "BotGroups" SET "LastError"=%s,"LastAdAt"=now(),'
                    '"IsMember"=CASE WHEN %s THEN false ELSE "IsMember" END,"UpdatedAt"=now() '
                    'WHERE "ChatId"=%s', (str(error)[:500], bool(left), int(chat_id)))
        conn.commit()


# ── ارسال ────────────────────────────────────────────────────────────────

async def post_ad(bot, group):
    """پیام قبلی را پاک کن، تبلیغ تازه را بفرست. خروجی: شناسهٔ پیام تازه."""
    from telegram.error import BadRequest, Forbidden

    import asyncio

    chat_id = group['chat_id']
    if group.get('last_message_id'):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=int(group['last_message_id']))
        except Exception:  # noqa: BLE001 — پاک شده یا قدیمی‌تر از ۴۸ ساعت
            _LOG.info('old ad in %s could not be deleted', chat_id)
    text = await asyncio.to_thread(ad_text)
    markup = ad_keyboard(getattr(bot, 'username', '') or '')
    try:
        try:
            message = await bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML',
                                             reply_markup=markup, disable_web_page_preview=True)
        except BadRequest as exc:
            if "can't parse entities" not in str(exc).lower():
                raise
            # متن سفارشی مدیر HTML نامعتبر دارد: همان متن، ساده.
            message = await bot.send_message(chat_id=chat_id, text=html.unescape(text),
                                             reply_markup=markup, disable_web_page_preview=True)
    except Forbidden as exc:
        await asyncio.to_thread(record_error, chat_id, f'ربات از گروه بیرون است: {exc}', left=True)
        return None
    except BadRequest as exc:
        await asyncio.to_thread(record_error, chat_id, str(exc))
        return None
    await asyncio.to_thread(record_ad, chat_id, message.message_id)
    return message.message_id


async def run_due(bot, *, force=False):
    """تبلیغ را در گروه‌هایی که نوبتشان است بفرست. خروجی: تعداد ارسال موفق."""
    import asyncio

    groups = await asyncio.to_thread(list_groups) if force else await asyncio.to_thread(due_groups)
    sent = 0
    for group in groups:
        if not group['is_member'] or not group['ads']:
            continue
        try:
            if await post_ad(bot, group):
                sent += 1
        except Exception as exc:  # noqa: BLE001 — یک گروه نباید بقیه را متوقف کند
            _LOG.exception('group ad failed chat=%s', group['chat_id'])
            await asyncio.to_thread(record_error, group['chat_id'], str(exc))
        await asyncio.sleep(1.5)
    return sent


async def group_ads_loop(app):
    import asyncio

    await asyncio.sleep(60)
    while True:
        try:
            sent = await run_due(app.bot)
            if sent:
                _LOG.info('group ads posted: %s', sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception('group ads loop failed')
        await asyncio.sleep(10 * 60)


def status_line(group):
    name = html.escape(group['title'] or str(group['chat_id']))
    if not group['is_member']:
        state = '⚪️ ربات عضو نیست'
    elif not group['ads']:
        state = '⏸ تبلیغ خاموش'
    else:
        state = '🟢 تبلیغ روشن'
    when = '—'
    if group.get('last_ad_at'):
        from zoneinfo import ZoneInfo
        when = group['last_ad_at'].astimezone(ZoneInfo('Asia/Tehran')).strftime('%m/%d %H:%M')
    line = f'• <b>{name}</b> — {state} · آخرین تبلیغ: {when}'
    if group.get('last_error'):
        line += f'\n   ⚠️ {html.escape(group["last_error"][:120])}'
    return line
