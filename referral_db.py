"""دیتابیس بخش «دعوت دوستان» (رفرال) و مسابقه‌های دوره‌ای.

کاملاً افزایشی است: فقط جدول‌های BotReferrals / BotReferralCampaigns و کلیدهای
referral_* جدول BotSettings را می‌سازد و می‌نویسد. به سفارش، پرداخت و کیف پول
دست نمی‌زند؛ جدول Orders فقط برای شمارش «دعوت‌شده‌ی خریدار» خوانده می‌شود.
"""
import json
import logging
import re
import threading
import time
from datetime import timedelta, timezone

import db

_LOG = logging.getLogger(__name__)

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))
QUALIFIED_STATUSES = ('paid', 'processing', 'delivered', 'completed')
COUNT_MODES = ('join', 'purchase')
PAYLOAD_KINDS = ('', 'gem', 'gift')
MAX_CAMPAIGN_HOURS = 365 * 24

_PAYLOAD_RE = re.compile(r'^ref(gem|gift)?_(\d{3,20})$')

DEFAULT_SETTINGS = {
    'referral_enabled': '0',
    'referral_count_mode': 'join',
    'referral_notify_referrer': '1',
    'referral_invitee_welcome': '1',
    'referral_top_n': '10',
    'referral_banner_photo': '',
    'referral_campaign_title': '🏆 مسابقه دعوت دوستان Atomic Shop',
    'referral_prize_text': '🎁 جوایز این دوره به‌زودی اعلام می‌شود — آماده باش!',
    'referral_page_text': (
        "{title}\n"
        "━━━━━━━━━━━━━━━\n"
        "🔥 *{name}*، دوستاتو به *Atomic Shop* دعوت کن و *جایزه* ببر!\n\n"
        "🎁 *جوایز این دوره:*\n"
        "{prize}\n\n"
        "{deadline}\n\n"
        "📊 *وضعیت تو:*\n"
        "👥 امتیاز این دوره: *{count}*\n"
        "🏅 رتبه‌ی فعلی: *{rank}*\n"
        "{gap}\n"
        "🤝 کل دعوت‌ها: *{total}* · خریدار: *{bought}*\n\n"
        "{rule}\n\n"
        "👇 دکمه‌ی «📨 ارسال بنر برای دوستان» رو بزن و بنر رو برای دوستات و گروه‌ها بفرست؛ "
        "هر کی از بنر تو وارد ربات بشه، امتیازش مال توست ✅"
    ),
    'referral_banner_text': (
        "💎 *Atomic Shop | فروشگاه جم فری‌فایر* 💎\n"
        "━━━━━━━━━━━━━━━\n"
        "⚡️ خرید جم فری‌فایر با آیدی — *تحویل لحظه‌ای*\n"
        "⭐ استارز تلگرام · 🎁 گیفت کارت · 🎯 پک سنس\n"
        "💳 پرداخت امن با درگاه یا کارت‌به‌کارت\n\n"
        "🏆 *مسابقه‌ی جایزه‌دار در جریانه!*\n"
        "{prize}\n\n"
        "🎟 دعوت ویژه از طرف *{inviter}*\n"
        "👇 از دکمه‌ی زیر وارد مسابقه شو 👇"
    ),
    'referral_invitee_text': (
        "🎉 *{name}* عزیز، خوش اومدی!\n"
        "تو با دعوت *{inviter}* وارد *Atomic Shop* شدی 🤝\n\n"
        "💎 جم فری‌فایر با آیدی — تحویل لحظه‌ای و قیمت عالی\n\n"
        "🏆 *مسابقه دعوت دوستان در جریانه:*\n"
        "{prize}\n\n"
        "{deadline}\n\n"
        "👇 همین الان خریدت رو انجام بده یا لینک اختصاصی خودت رو بگیر و تو هم جایزه ببر!"
    ),
    'referral_notify_text': (
        "🎉 *تبریک {name}!*\n"
        "یک دوست جدید با بنر دعوت تو وارد ربات شد 🙌\n"
        "👤 دوست جدید: {friend}\n\n"
        "👥 امتیاز این دوره: *{count}*\n"
        "🏅 رتبه‌ی فعلی: *{rank}*\n"
        "{gap}\n"
        "{rule}\n\n"
        "🔥 ادامه بده، جایزه نزدیکه! 🏆"
    ),
    'referral_announce_text': (
        "🔥 *{title}* 🔥\n"
        "━━━━━━━━━━━━━━━\n"
        "دوستاتو به ربات دعوت کن، امتیاز جمع کن و *جایزه* ببر! 🏆\n\n"
        "🎁 *جوایز:*\n"
        "{prize}\n\n"
        "{deadline}\n"
        "{rule}\n\n"
        "👇 روی دکمه‌ی زیر بزن و بنر اختصاصی خودت رو بگیر"
    ),
    'referral_btn_gift': '🔥 شرکت در مسابقه جایزه‌دار',
}

# متن‌هایی که «بازگشت همه به پیش‌فرض» پاک می‌کند (تنظیمات روشن/خاموش دست نمی‌خورد).
TEXT_KEYS = (
    'referral_campaign_title', 'referral_prize_text', 'referral_page_text',
    'referral_banner_text', 'referral_invitee_text', 'referral_notify_text',
    'referral_announce_text', 'referral_btn_gift',
)

_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS "BotReferrals" (
        "InviteeTelegramId" VARCHAR(64) PRIMARY KEY,
        "InviteeUserId" INTEGER REFERENCES "Users"("Id") ON DELETE CASCADE,
        "ReferrerTelegramId" VARCHAR(64) NOT NULL,
        "ReferrerUserId" INTEGER NOT NULL REFERENCES "Users"("Id") ON DELETE CASCADE,
        "Source" VARCHAR(20) NOT NULL DEFAULT 'link',
        "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
    )''',
    '''CREATE INDEX IF NOT EXISTS idx_bot_referrals_referrer
       ON "BotReferrals" ("ReferrerUserId", "CreatedAt")''',
    '''CREATE INDEX IF NOT EXISTS idx_bot_referrals_created
       ON "BotReferrals" ("CreatedAt")''',
    '''CREATE TABLE IF NOT EXISTS "BotReferralCampaigns" (
        "Id" SERIAL PRIMARY KEY,
        "Title" VARCHAR(200) NOT NULL DEFAULT '',
        "PrizeText" TEXT NOT NULL DEFAULT '',
        "CountMode" VARCHAR(20) NOT NULL DEFAULT 'join',
        "StartsAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
        "EndsAt" TIMESTAMPTZ,
        "ClosedAt" TIMESTAMPTZ,
        "Winners" TEXT NOT NULL DEFAULT '[]'
    )''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_referral_campaign_open
       ON "BotReferralCampaigns" (("ClosedAt" IS NULL)) WHERE "ClosedAt" IS NULL''',
)

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SETTINGS_TTL_SECONDS = 30
_settings_cache = {'at': 0.0, 'values': None}


# ─── Schema ─────────────────────────────────────────────────────────────────────
def ensure_referral_schema():
    """Idempotent؛ روی دیتابیس فعلی فقط جدول‌های جدید رفرال را می‌سازد."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return True
        with db.get_conn() as conn, conn.cursor() as cur:
            for stmt in _SCHEMA:
                cur.execute(stmt)
            conn.commit()
        _SCHEMA_READY = True
    return True


# ─── Settings ───────────────────────────────────────────────────────────────────
def is_on(values, key):
    return str((values or {}).get(key, DEFAULT_SETTINGS.get(key, '')) or '').strip().lower() in (
        '1', 'true', 'on', 'yes',
    )


def invalidate_settings():
    _settings_cache.update(at=0.0, values=None)


def _load_stored_settings():
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Key", "Value" FROM "BotSettings" WHERE "Key" LIKE %s',
            ('referral\\_%',),
        )
        return {str(k): v for k, v in cur.fetchall()}


def settings(force=False):
    """تنظیمات رفرال با پیش‌فرض‌ها؛ مقدار خالی یعنی «پیش‌فرض»."""
    now = time.monotonic()
    cached = _settings_cache['values']
    if (
        not force and cached is not None
        and now - _settings_cache['at'] < _SETTINGS_TTL_SECONDS
    ):
        return cached
    try:
        stored = _load_stored_settings()
    except Exception:
        _LOG.warning('Referral settings could not be loaded', exc_info=True)
        return cached if cached is not None else dict(DEFAULT_SETTINGS)
    values = dict(DEFAULT_SETTINGS)
    for key, value in stored.items():
        if key in values and str(value or '').strip() != '':
            values[key] = str(value)
    if values['referral_count_mode'] not in COUNT_MODES:
        values['referral_count_mode'] = 'join'
    _settings_cache.update(at=now, values=values)
    return values


def put(key, value):
    if key not in DEFAULT_SETTINGS:
        raise ValueError('کلید تنظیم رفرال نامعتبر است.')
    db.set_setting(key, '' if value is None else str(value))
    invalidate_settings()


def top_n(values=None):
    try:
        return max(3, min(int((values or settings()).get('referral_top_n') or 10), 50))
    except (TypeError, ValueError):
        return 10


def menu_visible():
    """برای main_menu (همگام): بدون باز کردن اتصال تازه، تا منو کند نشود."""
    if db._POOL is None:
        return is_on(_settings_cache['values'] or {}, 'referral_enabled')
    try:
        return is_on(settings(), 'referral_enabled')
    except Exception:
        return False


# ─── Links ──────────────────────────────────────────────────────────────────────
def build_payload(telegram_id, kind=''):
    kind = kind if kind in PAYLOAD_KINDS else ''
    return f'ref{kind}_{int(telegram_id)}'


def parse_payload(raw):
    match = _PAYLOAD_RE.match(str(raw or '').strip())
    if not match:
        return None, ''
    return int(match.group(2)), match.group(1) or ''


def referral_link(bot_username, telegram_id, kind=''):
    username = str(bot_username or '').lstrip('@')
    return f'https://t.me/{username}?start={build_payload(telegram_id, kind)}'


# ─── Formatting ─────────────────────────────────────────────────────────────────
def to_jalali(gy, gm, gd):
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
        + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    )
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def _local(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TEHRAN_TZ)


def format_date(value):
    if value is None:
        return '—'
    local = _local(value)
    jy, jm, jd = to_jalali(local.year, local.month, local.day)
    return f'{jy}/{jm:02d}/{jd:02d}'


def format_dt(value):
    if value is None:
        return '—'
    return f'{format_date(value)} ساعت {_local(value):%H:%M}'


def format_remaining(ends_at, now):
    if ends_at is None or now is None:
        return ''
    seconds = int((ends_at - now).total_seconds())
    if seconds <= 0:
        return ''
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f'{days} روز')
    if hours:
        parts.append(f'{hours} ساعت')
    if not days and minutes:
        parts.append(f'{minutes} دقیقه')
    return ' و '.join(parts) or 'کمتر از یک دقیقه'


def display_name(first='', last='', username=''):
    name = f'{first or ""} {last or ""}'.strip()
    handle = str(username or '').lstrip('@').strip()
    if name and handle:
        return f'{name} (@{handle})'
    return name or (f'@{handle}' if handle else 'بدون نام')


def mask_name(first='', username=''):
    base = str(first or '').strip() or str(username or '').lstrip('@').strip() or 'کاربر'
    return (base[:2] if len(base) > 2 else base[:1]) + '•••'


# ─── Referrals ──────────────────────────────────────────────────────────────────
def record_referral(invitee_telegram_id, invitee_user_id, referrer_telegram_id, source='link'):
    """فقط برای کاربر تازه‌وارد صدا زده می‌شود؛ هر دعوت‌شده یک‌بار ثبت می‌شود."""
    try:
        invitee_tg = int(invitee_telegram_id)
        referrer_tg = int(referrer_telegram_id)
        invitee_uid = int(invitee_user_id)
    except (TypeError, ValueError):
        return None
    if invitee_tg <= 0 or referrer_tg <= 0 or invitee_tg == referrer_tg:
        return None
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        # قفل دوم: کاربری که از قبل در ربات بوده (عضویت قدیمی، سفارش، تراکنش،
        # دعوت‌شده یا دعوت‌کننده قبلی) حتی با لینک دعوت هم امتیاز نمی‌دهد.
        cur.execute(
            'SELECT u."DateJoined" >= now() - interval \'1 hour\', '
            'u."ReferredById" IS NOT NULL, '
            'EXISTS (SELECT 1 FROM "Orders" o WHERE o."UserId"=u."Id"), '
            'EXISTS (SELECT 1 FROM "Wallets" w JOIN "WalletTransactions" t '
            'ON t."WalletId"=w."Id" WHERE w."UserId"=u."Id"), '
            'EXISTS (SELECT 1 FROM "BotReferrals" br WHERE br."ReferrerUserId"=u."Id") '
            'FROM "Users" u WHERE u."Id"=%s AND u."TelegramId"=%s',
            (invitee_uid, str(invitee_tg)),
        )
        fresh = cur.fetchone()
        if not fresh or not fresh[0] or any(fresh[1:]):
            return None
        cur.execute(
            'SELECT "Id", COALESCE("IsBlocked", false), "FirstName", "TelegramUsername" '
            'FROM "Users" WHERE "TelegramId"=%s ORDER BY "Id" LIMIT 1',
            (str(referrer_tg),),
        )
        row = cur.fetchone()
        if not row or row[1] or int(row[0]) == invitee_uid:
            return None
        referrer_uid = int(row[0])
        cur.execute(
            'INSERT INTO "BotReferrals" '
            '("InviteeTelegramId", "InviteeUserId", "ReferrerTelegramId", '
            '"ReferrerUserId", "Source") VALUES (%s, %s, %s, %s, %s) '
            'ON CONFLICT ("InviteeTelegramId") DO NOTHING RETURNING "CreatedAt"',
            (str(invitee_tg), invitee_uid, str(referrer_tg), referrer_uid,
             str(source or 'link')[:20]),
        )
        created = cur.fetchone()
        if not created:
            conn.rollback()
            return None
        # ستون قدیمی پنل («کاربران دارای زیرمجموعه») هم هماهنگ بماند.
        cur.execute(
            'UPDATE "Users" SET "ReferredById"=%s WHERE "Id"=%s AND "ReferredById" IS NULL',
            (referrer_uid, invitee_uid),
        )
        conn.commit()
    return {
        'referrer_user_id': referrer_uid,
        'referrer_telegram_id': str(referrer_tg),
        'referrer_first_name': row[2] or '',
        'referrer_username': row[3] or '',
        'created_at': created[0],
    }


_FROM = (
    'FROM "BotReferrals" r '
    'JOIN "Users" ru ON ru."Id"=r."ReferrerUserId" '
    'LEFT JOIN "Users" iu ON iu."Id"=r."InviteeUserId" '
)
_BOUGHT_SQL = (
    'EXISTS (SELECT 1 FROM "Orders" o WHERE o."UserId"=r."InviteeUserId" '
    'AND o."Status" IN (%s, %s, %s, %s))'
)


def _filters(campaign=None, mode='join', window=True):
    clauses = [
        'COALESCE(ru."IsBlocked", false)=false',
        'COALESCE(iu."IsBlocked", false)=false',
    ]
    params = []
    if window and campaign:
        clauses.append('r."CreatedAt">=%s')
        params.append(campaign['starts_at'])
        if campaign.get('ends_at') is not None:
            clauses.append('r."CreatedAt"<%s')
            params.append(campaign['ends_at'])
    if mode == 'purchase':
        clauses.append(_BOUGHT_SQL)
        params.extend(QUALIFIED_STATUSES)
    return ' AND '.join(clauses), params


def _leaderboard_cur(cur, campaign, mode, limit, offset=0, window=True):
    where, params = _filters(campaign, mode, window)
    cur.execute(
        'SELECT r."ReferrerUserId", ru."TelegramId", ru."FirstName", ru."LastName", '
        'ru."TelegramUsername", COUNT(*) AS cnt, MAX(r."CreatedAt") AS last_at '
        + _FROM + 'WHERE ' + where
        + ' GROUP BY r."ReferrerUserId", ru."TelegramId", ru."FirstName", '
        'ru."LastName", ru."TelegramUsername" '
        'ORDER BY cnt DESC, last_at ASC, r."ReferrerUserId" ASC LIMIT %s OFFSET %s',
        (*params, int(limit), int(offset)),
    )
    return [
        {
            'rank': int(offset) + index + 1,
            'user_id': int(row[0]),
            'telegram_id': str(row[1] or ''),
            'first_name': row[2] or '',
            'last_name': row[3] or '',
            'username': row[4] or '',
            'count': int(row[5] or 0),
            'last_at': row[6],
        }
        for index, row in enumerate(cur.fetchall())
    ]


def leaderboard(campaign=None, mode='join', limit=10, offset=0, window=True):
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        return _leaderboard_cur(cur, campaign, mode, limit, offset, window)


def count_leaderboard(campaign=None, mode='join', window=True):
    ensure_referral_schema()
    where, params = _filters(campaign, mode, window)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT COUNT(DISTINCT r."ReferrerUserId"), COUNT(*) ' + _FROM + 'WHERE ' + where,
            params,
        )
        row = cur.fetchone() or (0, 0)
    return int(row[0] or 0), int(row[1] or 0)


def find_user(telegram_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id", "TelegramId", "FirstName", "LastName", "TelegramUsername", '
            'COALESCE("IsBlocked", false), "DateJoined" FROM "Users" '
            'WHERE "TelegramId"=%s ORDER BY "Id" LIMIT 1',
            (str(telegram_id),),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        'id': int(row[0]),
        'telegram_id': str(row[1] or ''),
        'first_name': row[2] or '',
        'last_name': row[3] or '',
        'username': row[4] or '',
        'blocked': bool(row[5]),
        'joined_at': row[6],
    }


def user_stats(telegram_id, campaign=None, mode='join'):
    """امتیاز دوره، رتبه، کل دعوت‌ها و تعداد دعوت‌شده‌های خریدار."""
    empty = {
        'user_id': None, 'count': 0, 'rank': None, 'total': 0, 'bought': 0,
        'last_at': None, 'above_count': None, 'below_count': None,
    }
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT "Id" FROM "Users" WHERE "TelegramId"=%s ORDER BY "Id" LIMIT 1',
            (str(telegram_id),),
        )
        row = cur.fetchone()
        if not row:
            return empty
        uid = int(row[0])
        where, params = _filters(campaign, mode, True)
        # «جلوتر از من» با همان ترتیب جدول: امتیاز بیشتر، یا برابر ولی زودتر رسیده.
        ahead = (
            '(c.cnt>me.cnt OR (c.cnt=me.cnt AND (c.last_at<me.last_at '
            'OR (c.last_at=me.last_at AND c.uid<me.uid))))'
        )
        cur.execute(
            'WITH counts AS (SELECT r."ReferrerUserId" AS uid, COUNT(*) AS cnt, '
            'MAX(r."CreatedAt") AS last_at ' + _FROM + 'WHERE ' + where
            + ' GROUP BY r."ReferrerUserId") '
            'SELECT me.cnt, '
            f'(SELECT COUNT(*) FROM counts c WHERE {ahead}), '
            f'(SELECT MIN(c.cnt) FROM counts c WHERE {ahead}), '
            f'(SELECT MAX(c.cnt) FROM counts c WHERE c.uid<>me.uid AND NOT {ahead}) '
            'FROM counts me WHERE me.uid=%s',
            (*params, uid),
        )
        ranked = cur.fetchone()
        cur.execute(
            'SELECT COUNT(*), COUNT(*) FILTER (WHERE ' + _BOUGHT_SQL + '), MAX(r."CreatedAt") '
            'FROM "BotReferrals" r WHERE r."ReferrerUserId"=%s',
            (*QUALIFIED_STATUSES, uid),
        )
        totals = cur.fetchone() or (0, 0, None)
    return {
        'user_id': uid,
        'count': int(ranked[0] or 0) if ranked else 0,
        'rank': int(ranked[1] or 0) + 1 if ranked else None,
        'total': int(totals[0] or 0),
        'bought': int(totals[1] or 0),
        'last_at': totals[2],
        # امتیاز نفر درست بالایی و درست پایینی در جدول (برای «فاصله با نفر بعدی»)
        'above_count': int(ranked[2]) if ranked and ranked[2] is not None else None,
        'below_count': int(ranked[3]) if ranked and ranked[3] is not None else None,
    }


def list_invitees(referrer_user_id, limit=20, offset=0):
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT COUNT(*) FROM "BotReferrals" WHERE "ReferrerUserId"=%s',
            (int(referrer_user_id),),
        )
        total = int((cur.fetchone() or (0,))[0] or 0)
        cur.execute(
            'SELECT r."InviteeTelegramId", iu."FirstName", iu."LastName", '
            'iu."TelegramUsername", r."CreatedAt", ' + _BOUGHT_SQL + ', '
            'COALESCE(iu."IsBlocked", false) '
            'FROM "BotReferrals" r LEFT JOIN "Users" iu ON iu."Id"=r."InviteeUserId" '
            'WHERE r."ReferrerUserId"=%s ORDER BY r."CreatedAt" DESC LIMIT %s OFFSET %s',
            (*QUALIFIED_STATUSES, int(referrer_user_id), int(limit), int(offset)),
        )
        rows = cur.fetchall()
    return [
        {
            'telegram_id': str(row[0] or ''),
            'first_name': row[1] or '',
            'last_name': row[2] or '',
            'username': row[3] or '',
            'created_at': row[4],
            'bought': bool(row[5]),
            'blocked': bool(row[6]),
        }
        for row in rows
    ], total


def overview(campaign=None, mode='join'):
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT COUNT(*), COUNT(DISTINCT "ReferrerUserId"), '
            'COUNT(*) FILTER (WHERE "CreatedAt">=now()-interval \'24 hours\') '
            'FROM "BotReferrals"'
        )
        total, referrers, last_day = cur.fetchone() or (0, 0, 0)
        where, params = _filters(campaign, mode, True)
        cur.execute(
            'SELECT COUNT(DISTINCT r."ReferrerUserId"), COUNT(*) ' + _FROM + 'WHERE ' + where,
            params,
        )
        period_referrers, period_points = cur.fetchone() or (0, 0)
    return {
        'total': int(total or 0),
        'referrers': int(referrers or 0),
        'last_day': int(last_day or 0),
        'period_referrers': int(period_referrers or 0),
        'period_points': int(period_points or 0),
    }


def all_referrer_telegram_ids():
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT DISTINCT ru."TelegramId" FROM "BotReferrals" r '
            'JOIN "Users" ru ON ru."Id"=r."ReferrerUserId" '
            'WHERE COALESCE(ru."IsBlocked", false)=false AND ru."TelegramId" IS NOT NULL'
        )
        return [str(row[0]) for row in cur.fetchall() if str(row[0] or '').isdigit()]


# ─── Campaigns ──────────────────────────────────────────────────────────────────
_CAMPAIGN_COLS = (
    '"Id", "Title", "PrizeText", "CountMode", "StartsAt", "EndsAt", '
    '"ClosedAt", "Winners", now()'
)


def _campaign_from_row(row):
    if not row:
        return None
    cid, title, prize, mode, starts, ends, closed, winners, now = row
    try:
        parsed = json.loads(winners or '[]')
    except (TypeError, ValueError):
        parsed = []
    return {
        'id': int(cid),
        'title': title or '',
        'prize_text': prize or '',
        'count_mode': mode or 'join',
        'starts_at': starts,
        'ends_at': ends,
        'closed_at': closed,
        'winners': parsed if isinstance(parsed, list) else [],
        'now': now,
        'ended': bool(ends is not None and now is not None and ends <= now),
    }


def active_campaign():
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_CAMPAIGN_COLS} FROM "BotReferralCampaigns" '
            'WHERE "ClosedAt" IS NULL ORDER BY "Id" DESC LIMIT 1'
        )
        return _campaign_from_row(cur.fetchone())


def get_campaign(campaign_id):
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_CAMPAIGN_COLS} FROM "BotReferralCampaigns" WHERE "Id"=%s',
            (int(campaign_id),),
        )
        return _campaign_from_row(cur.fetchone())


def list_campaigns(limit=10):
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT {_CAMPAIGN_COLS} FROM "BotReferralCampaigns" '
            'WHERE "ClosedAt" IS NOT NULL ORDER BY "Id" DESC LIMIT %s',
            (int(limit),),
        )
        return [_campaign_from_row(row) for row in cur.fetchall()]


def _close_open_campaigns_cur(cur, values, winners_limit):
    cur.execute(
        f'SELECT {_CAMPAIGN_COLS} FROM "BotReferralCampaigns" '
        'WHERE "ClosedAt" IS NULL ORDER BY "Id" FOR UPDATE'
    )
    campaigns = [_campaign_from_row(row) for row in cur.fetchall()]
    mode = values.get('referral_count_mode') or 'join'
    closed = []
    for campaign in campaigns:
        ends = campaign['ends_at'] if campaign['ended'] else campaign['now']
        window = dict(campaign, ends_at=ends)
        winners = [
            {
                'rank': entry['rank'],
                'telegram_id': entry['telegram_id'],
                'name': display_name(entry['first_name'], entry['last_name'], entry['username']),
                'count': entry['count'],
            }
            for entry in _leaderboard_cur(cur, window, mode, winners_limit)
        ]
        cur.execute(
            'UPDATE "BotReferralCampaigns" SET "ClosedAt"=now(), "EndsAt"=%s, '
            '"Winners"=%s, "PrizeText"=%s, "CountMode"=%s WHERE "Id"=%s',
            (ends, json.dumps(winners, ensure_ascii=False),
             values.get('referral_prize_text') or '', mode, campaign['id']),
        )
        closed.append(dict(window, winners=winners, closed_at=campaign['now'],
                           prize_text=values.get('referral_prize_text') or '',
                           count_mode=mode))
    return closed


def close_campaign(winners_limit=10):
    """مسابقه باز را می‌بندد و برترین‌ها را به‌عنوان برنده ذخیره می‌کند."""
    ensure_referral_schema()
    values = settings(force=True)
    with db.get_conn() as conn, conn.cursor() as cur:
        closed = _close_open_campaigns_cur(cur, values, winners_limit)
        conn.commit()
    return closed[-1] if closed else None


def _checked_hours(hours):
    hours = int(hours)
    if not 1 <= hours <= MAX_CAMPAIGN_HOURS:
        raise ValueError('مدت مسابقه باید بین ۱ ساعت تا ۳۶۵ روز باشد.')
    return hours


def start_campaign(hours, title='', winners_limit=10):
    """مسابقه قبلی (اگر باز بود) با برندگانش بسته و مسابقه تازه از همین لحظه شروع می‌شود."""
    hours = _checked_hours(hours)
    ensure_referral_schema()
    values = settings(force=True)
    with db.get_conn() as conn, conn.cursor() as cur:
        closed = _close_open_campaigns_cur(cur, values, winners_limit)
        cur.execute(
            'INSERT INTO "BotReferralCampaigns" ("Title", "PrizeText", "CountMode", '
            '"StartsAt", "EndsAt") VALUES (%s, %s, %s, now(), now()+make_interval(hours => %s)) '
            'RETURNING "Id"',
            (str(title or '')[:200], values.get('referral_prize_text') or '',
             values.get('referral_count_mode') or 'join', hours),
        )
        campaign_id = int(cur.fetchone()[0])
        conn.commit()
    return campaign_id, closed


def set_campaign_end(hours_from_now):
    hours = _checked_hours(hours_from_now)
    ensure_referral_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "BotReferralCampaigns" SET "EndsAt"=now()+make_interval(hours => %s) '
            'WHERE "ClosedAt" IS NULL RETURNING "Id"',
            (hours,),
        )
        row = cur.fetchone()
        conn.commit()
    return int(row[0]) if row else None
