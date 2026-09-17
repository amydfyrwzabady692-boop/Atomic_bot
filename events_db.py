"""دیتابیس بخش «رویداد جدید بازی» (اعلام آیتم‌های تازه در چنل‌ها).

کاملاً افزایشی: فقط جدول‌های BotGameEvents / BotEventPosts / BotEventChannels /
BotEventClicks و کلیدهای event_* جدول BotSettings را می‌سازد و می‌نویسد.
به سفارش، پرداخت، کیف پول و بقیه بخش‌ها دست نمی‌زند.
"""
import json
import logging
import threading
import time

import db

_LOG = logging.getLogger(__name__)

DEFAULT_SITE_URL = 'https://atomicshop.ir'

DEFAULT_SETTINGS = {
    'event_btn_bot': '💎 خرید جم در ربات · تحویل لحظه‌ای',
    'event_btn_site': '🌐 خرید جم از سایت Atomic Shop',
    'event_site_url': DEFAULT_SITE_URL,
    'event_btn_bot_emoji': '',
    'event_btn_site_emoji': '',
    'event_default_pin': '0',
    'event_default_notify': '0',
    'event_default_site': '1',
}

EVENT_FIELDS = {
    'photo_file_id': '"PhotoFileId"',
    'body': '"Body"',
    'body_entities': '"BodyEntities"',
    'extra_btn_text': '"ExtraBtnText"',
    'extra_btn_url': '"ExtraBtnUrl"',
    'show_site': '"ShowSite"',
    'pin': '"Pin"',
    'notify_users': '"NotifyUsers"',
    'target_chats': '"TargetChats"',
    'status': '"Status"',
}

_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS "BotEventChannels" (
        "Id" SERIAL PRIMARY KEY,
        "ChatId" VARCHAR(64) NOT NULL UNIQUE,
        "Title" VARCHAR(255) NOT NULL DEFAULT '',
        "Username" VARCHAR(150) NOT NULL DEFAULT '',
        "ChatType" VARCHAR(20) NOT NULL DEFAULT 'channel',
        "CanPost" BOOLEAN NOT NULL DEFAULT true,
        "IsActive" BOOLEAN NOT NULL DEFAULT true,
        "UpdatedAt" TIMESTAMPTZ NOT NULL DEFAULT now()
    )''',
    '''CREATE TABLE IF NOT EXISTS "BotGameEvents" (
        "Id" SERIAL PRIMARY KEY,
        "PhotoFileId" TEXT NOT NULL DEFAULT '',
        "Body" TEXT NOT NULL DEFAULT '',
        "BodyEntities" TEXT NOT NULL DEFAULT '[]',
        "ExtraBtnText" VARCHAR(64) NOT NULL DEFAULT '',
        "ExtraBtnUrl" VARCHAR(500) NOT NULL DEFAULT '',
        "ShowSite" BOOLEAN NOT NULL DEFAULT true,
        "Pin" BOOLEAN NOT NULL DEFAULT false,
        "NotifyUsers" BOOLEAN NOT NULL DEFAULT false,
        "TargetChats" TEXT,
        "Status" VARCHAR(20) NOT NULL DEFAULT 'draft',
        "Clicks" INTEGER NOT NULL DEFAULT 0,
        "CreatedBy" VARCHAR(64) NOT NULL DEFAULT '',
        "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
        "PublishedAt" TIMESTAMPTZ,
        "NotifiedAt" TIMESTAMPTZ
    )''',
    '''CREATE TABLE IF NOT EXISTS "BotEventPosts" (
        "Id" SERIAL PRIMARY KEY,
        "EventId" INTEGER NOT NULL REFERENCES "BotGameEvents"("Id") ON DELETE CASCADE,
        "ChatId" VARCHAR(64) NOT NULL,
        "MessageId" BIGINT NOT NULL,
        "Kind" VARCHAR(10) NOT NULL DEFAULT 'text',
        "PostedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE ("EventId", "ChatId", "MessageId")
    )''',
    '''CREATE INDEX IF NOT EXISTS idx_bot_event_posts_event
       ON "BotEventPosts" ("EventId")''',
    '''CREATE TABLE IF NOT EXISTS "BotEventClicks" (
        "EventId" INTEGER NOT NULL REFERENCES "BotGameEvents"("Id") ON DELETE CASCADE,
        "TelegramId" VARCHAR(64) NOT NULL,
        "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY ("EventId", "TelegramId")
    )''',
)

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SETTINGS_TTL = 30
_settings_cache = {'at': 0.0, 'values': None}


def ensure_events_schema():
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


def settings(force=False):
    now = time.monotonic()
    cached = _settings_cache['values']
    if not force and cached is not None and now - _settings_cache['at'] < _SETTINGS_TTL:
        return cached
    values = dict(DEFAULT_SETTINGS)
    try:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                'SELECT "Key", "Value" FROM "BotSettings" WHERE "Key" LIKE %s',
                ('event\\_%',),
            )
            for key, value in cur.fetchall():
                if key in values and str(value or '').strip() != '':
                    values[key] = str(value)
    except Exception:
        _LOG.warning('Event settings could not be loaded', exc_info=True)
        return cached if cached is not None else values
    _settings_cache.update(at=now, values=values)
    return values


def put(key, value):
    if key not in DEFAULT_SETTINGS:
        raise ValueError('کلید تنظیم رویداد نامعتبر است.')
    db.set_setting(key, '' if value is None else str(value))
    invalidate_settings()


# ─── Channels ───────────────────────────────────────────────────────────────────
_CHANNEL_COLS = '"Id", "ChatId", "Title", "Username", "ChatType", "CanPost", "IsActive"'


def _channel(row):
    if not row:
        return None
    return {
        'id': int(row[0]), 'chat_id': str(row[1]), 'title': row[2] or '',
        'username': row[3] or '', 'chat_type': row[4] or 'channel',
        'can_post': bool(row[5]), 'active': bool(row[6]),
    }


def upsert_channel(chat_id, title='', username='', chat_type='channel', can_post=True, active=True):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "BotEventChannels" ("ChatId","Title","Username","ChatType","CanPost","IsActive") '
            'VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT ("ChatId") DO UPDATE SET '
            '"Title"=EXCLUDED."Title", "Username"=EXCLUDED."Username", '
            '"ChatType"=EXCLUDED."ChatType", "CanPost"=EXCLUDED."CanPost", '
            '"IsActive"=EXCLUDED."IsActive", "UpdatedAt"=now() '
            f'RETURNING {_CHANNEL_COLS}',
            (str(chat_id), str(title or '')[:255], str(username or '').lstrip('@')[:150],
             str(chat_type or 'channel')[:20], bool(can_post), bool(active)),
        )
        row = cur.fetchone()
        conn.commit()
    return _channel(row)


def deactivate_channel(chat_id):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "BotEventChannels" SET "IsActive"=false, "UpdatedAt"=now() WHERE "ChatId"=%s',
            (str(chat_id),),
        )
        conn.commit()
        return cur.rowcount > 0


def list_channels(active_only=True):
    ensure_events_schema()
    where = 'WHERE "IsActive"=true AND "CanPost"=true ' if active_only else ''
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT {_CHANNEL_COLS} FROM "BotEventChannels" {where}ORDER BY "Id"')
        return [_channel(row) for row in cur.fetchall()]


def get_channel(row_id):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT {_CHANNEL_COLS} FROM "BotEventChannels" WHERE "Id"=%s', (int(row_id),))
        return _channel(cur.fetchone())


def remove_channel(row_id):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM "BotEventChannels" WHERE "Id"=%s', (int(row_id),))
        conn.commit()
        return cur.rowcount > 0


# ─── Events ─────────────────────────────────────────────────────────────────────
_EVENT_COLS = (
    'e."Id", e."PhotoFileId", e."Body", e."BodyEntities", e."ExtraBtnText", e."ExtraBtnUrl", '
    'e."ShowSite", e."Pin", e."NotifyUsers", e."TargetChats", e."Status", e."Clicks", '
    'e."CreatedBy", e."CreatedAt", e."PublishedAt", e."NotifiedAt", '
    '(SELECT COUNT(*) FROM "BotEventPosts" p WHERE p."EventId"=e."Id"), '
    '(SELECT COUNT(*) FROM "BotEventClicks" c WHERE c."EventId"=e."Id")'
)


def _event(row):
    if not row:
        return None
    try:
        targets = json.loads(row[9]) if row[9] else None
        if targets is not None:
            targets = [int(x) for x in targets]
    except (TypeError, ValueError):
        targets = None
    return {
        'id': int(row[0]), 'photo_file_id': row[1] or '', 'body': row[2] or '',
        'body_entities': row[3] or '[]', 'extra_btn_text': row[4] or '',
        'extra_btn_url': row[5] or '', 'show_site': bool(row[6]), 'pin': bool(row[7]),
        'notify_users': bool(row[8]), 'target_chats': targets, 'status': row[10] or 'draft',
        'clicks': int(row[11] or 0), 'created_by': row[12] or '', 'created_at': row[13],
        'published_at': row[14], 'notified_at': row[15], 'posts': int(row[16] or 0),
        'unique_clicks': int(row[17] or 0),
    }


def event_title(event, limit=40):
    body = str((event or {}).get('body') or '').strip()
    first = body.splitlines()[0].strip() if body else ''
    if not first:
        first = '🖼 رویداد تصویری' if (event or {}).get('photo_file_id') else 'رویداد'
    return first[:limit]


def create_event(created_by, defaults=None):
    ensure_events_schema()
    values = defaults or settings()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "BotGameEvents" ("CreatedBy","ShowSite","Pin","NotifyUsers") '
            'VALUES (%s,%s,%s,%s) RETURNING "Id"',
            (str(created_by or ''), is_on(values, 'event_default_site'),
             is_on(values, 'event_default_pin'), is_on(values, 'event_default_notify')),
        )
        event_id = int(cur.fetchone()[0])
        conn.commit()
    return event_id


def get_event(event_id):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f'SELECT {_EVENT_COLS} FROM "BotGameEvents" e WHERE e."Id"=%s', (int(event_id),))
        return _event(cur.fetchone())


def update_event(event_id, **fields):
    if not fields:
        return get_event(event_id)
    unknown = set(fields) - set(EVENT_FIELDS)
    if unknown:
        raise ValueError(f'فیلد نامعتبر: {", ".join(sorted(unknown))}')
    sets, params = [], []
    for key, value in fields.items():
        if key == 'target_chats' and value is not None:
            value = json.dumps(sorted({int(v) for v in value}))
        sets.append(f'{EVENT_FIELDS[key]}=%s')
        params.append(value)
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'UPDATE "BotGameEvents" SET {", ".join(sets)} WHERE "Id"=%s',
            (*params, int(event_id)),
        )
        conn.commit()
    return get_event(event_id)


def mark_published(event_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "BotGameEvents" SET "Status"=\'published\', '
            '"PublishedAt"=COALESCE("PublishedAt", now()) WHERE "Id"=%s',
            (int(event_id),),
        )
        conn.commit()


def claim_notify(event_id):
    """فقط یک‌بار به کاربران ربات اطلاع داده شود."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "BotGameEvents" SET "NotifiedAt"=now() '
            'WHERE "Id"=%s AND "NotifiedAt" IS NULL RETURNING "Id"',
            (int(event_id),),
        )
        row = cur.fetchone()
        conn.commit()
    return bool(row)


def list_events(limit=10, offset=0):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM "BotGameEvents"')
        total = int(cur.fetchone()[0] or 0)
        cur.execute(
            f'SELECT {_EVENT_COLS} FROM "BotGameEvents" e ORDER BY e."Id" DESC LIMIT %s OFFSET %s',
            (int(limit), int(offset)),
        )
        return [_event(row) for row in cur.fetchall()], total


def delete_event(event_id):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM "BotGameEvents" WHERE "Id"=%s', (int(event_id),))
        conn.commit()
        return cur.rowcount > 0


# ─── Posts & clicks ─────────────────────────────────────────────────────────────
def record_post(event_id, chat_id, message_id, kind):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'INSERT INTO "BotEventPosts" ("EventId","ChatId","MessageId","Kind") '
            'VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (int(event_id), str(chat_id), int(message_id), str(kind)),
        )
        conn.commit()


def list_posts(event_id):
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'SELECT p."Id", p."ChatId", p."MessageId", p."Kind", COALESCE(c."Title", \'\') '
            'FROM "BotEventPosts" p LEFT JOIN "BotEventChannels" c ON c."ChatId"=p."ChatId" '
            'WHERE p."EventId"=%s ORDER BY p."Id"',
            (int(event_id),),
        )
        return [
            {'id': int(r[0]), 'chat_id': str(r[1]), 'message_id': int(r[2]),
             'kind': r[3], 'title': r[4]}
            for r in cur.fetchall()
        ]


def delete_post(post_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM "BotEventPosts" WHERE "Id"=%s', (int(post_id),))
        conn.commit()


def reset_status_if_unposted(event_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "BotGameEvents" e SET "Status"=\'draft\' WHERE e."Id"=%s AND NOT EXISTS '
            '(SELECT 1 FROM "BotEventPosts" p WHERE p."EventId"=e."Id")',
            (int(event_id),),
        )
        conn.commit()


def record_click(event_id, telegram_id):
    """هر زدن دکمه ربات را می‌شمارد؛ کاربر یکتا هم جدا ثبت می‌شود."""
    ensure_events_schema()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'UPDATE "BotGameEvents" SET "Clicks"="Clicks"+1 WHERE "Id"=%s RETURNING "Id"',
            (int(event_id),),
        )
        if not cur.fetchone():
            conn.rollback()
            return False
        cur.execute(
            'INSERT INTO "BotEventClicks" ("EventId","TelegramId") VALUES (%s,%s) '
            'ON CONFLICT DO NOTHING',
            (int(event_id), str(telegram_id)),
        )
        conn.commit()
    return True
