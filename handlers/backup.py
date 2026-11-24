"""💾 بک‌آپِ دیتابیسِ ربات و سایت از پنلِ مدیریت — فقط برای مالک (ADMIN_CHAT_ID).

کانتینرِ ربات فقط‌خواندنی است و /tmp آن ۳۲ مگ؛ برای همین فایلی روی دیسک ساخته
نمی‌شود: خروجیِ pg_dump همان لحظه gzip می‌شود و هر ۴۵ مگ (زیرِ سقفِ ۵۰ مگِ فایلِ
ربات‌ها در تلگرام) یک بخش می‌شود و فرستاده می‌شود. سایت (سرورِ ایران) به تلگرام
راه ندارد؛ ربات بک‌آپش را از ‎/internal/bot/backup/‎ می‌گیرد و همین‌جا می‌فرستد.

فایل‌ها اطلاعاتِ همهٔ کاربران را دارند، پس فقط مالک دکمه را می‌بیند و فقط به
خودِ او فرستاده می‌شود؛ ادمین‌های کمکی دسترسی ندارند.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from admin_notify import admin_id

log = logging.getLogger(__name__)

PART_BYTES = 45 * 1024 * 1024
SEND_TIMEOUT = 15 * 60
COOLDOWN_SECONDS = 60
_TIMEOUTS = {'read_timeout': 600, 'write_timeout': 600, 'connect_timeout': 60, 'pool_timeout': 60}
_TEHRAN = timezone(timedelta(hours=3, minutes=30))  # ایران از ۱۴۰۱ ساعتِ تابستانی ندارد
_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
# همهٔ کار در یک event loop است؛ یک پرچم کافی است.
_STATE = {'running': False, 'last': 0.0}

HOME_TEXT = (
    '💾 بک‌آپ دیتابیس\n'
    '━━━━━━━━━━━━━━━\n'
    'یک بک‌آپِ کامل از دیتابیسِ ربات و دیتابیسِ سایت گرفته می‌شود و همین‌جا برایت '
    'می‌آید؛ فقط برای مالکِ ربات.\n\n'
    '• فایلِ بزرگ‌تر از ۴۵ مگ چند بخش می‌شود (تلگرام از ربات فایلِ بالای ۵۰ مگ '
    'نمی‌گیرد)؛ راهنمای یکی‌کردنِ بخش‌ها آخرِ کار می‌آید.\n'
    '• بک‌آپِ ربات یکی دو دقیقه طول می‌کشد؛ ربات در این مدت کارش را می‌کند.\n'
    '• این فایل‌ها اطلاعاتِ همهٔ کاربران را دارند؛ جای امن نگهشان دار و برای کسی نفرست.'
)


def fa(value) -> str:
    return str(value).translate(_FA)


def mb(size) -> str:
    return fa(f'{int(size) / 1048576:.1f}').replace('.', '٫')


def is_owner(user_id) -> bool:
    owner = admin_id()
    try:
        return bool(owner) and int(user_id) == owner
    except (TypeError, ValueError):
        return False


class PartSplitter:
    """فایلِ نوشتنی که هر PART_BYTES بایت را به on_part(شماره، داده، آخری؟) می‌دهد.

    بخش فقط وقتی بیرون می‌رود که بایتِ بعدی رسیده باشد؛ پس آخرین بخش همیشه با
    finish() و last=True می‌رود و فایلِ تک‌بخشی پسوندِ ‎.part1‎ نمی‌گیرد.
    """

    def __init__(self, on_part, part_bytes=None):
        self._on_part = on_part
        self._size = int(part_bytes or PART_BYTES)
        self._buf = bytearray()
        self.parts = 0
        self.total = 0

    def write(self, data) -> int:
        self._buf += data
        self.total += len(data)
        while len(self._buf) > self._size:
            chunk = bytes(self._buf[:self._size])
            del self._buf[:self._size]
            self.parts += 1
            self._on_part(self.parts, chunk, False)
        return len(data)

    def flush(self):
        pass

    def finish(self):
        self.parts += 1
        chunk, self._buf = bytes(self._buf), bytearray()
        self._on_part(self.parts, chunk, True)


def _pg_env() -> dict:
    """همان اتصالِ db.py؛ رمز فقط در PGPASSWORD (نه در خطِ فرمانِ pg_dump)."""
    env = dict(os.environ)
    env.update({
        'PGHOST': os.getenv('DB_HOST', os.getenv('DB_SERVER', 'localhost')),
        'PGPORT': str(os.getenv('DB_PORT', '5432')),
        'PGDATABASE': os.getenv('DB_NAME', 'accshop'),
        'PGUSER': os.getenv('DB_USER', 'postgres'),
        'PGPASSWORD': os.getenv('DB_PASSWORD', 'postgres'),
        'PGCONNECT_TIMEOUT': '15',
    })
    return env


def dump_bot_database(on_part):
    """pg_dump (متنِ SQL، مثل بک‌آپ‌های دستیِ سرور) → gzip → بخش‌ها.

    خروجی: (تعدادِ بخش، حجمِ فایلِ فشرده). خطای pg_dump → RuntimeError؛ آن‌وقت
    بخشِ آخر فرستاده نمی‌شود و بخش‌های قبلی ناقص‌اند.
    """
    splitter = PartSplitter(on_part)
    proc = subprocess.Popen(['pg_dump', '--no-password', '--lock-wait-timeout=60s'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_pg_env())
    errors = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    reader.start()
    try:
        with gzip.GzipFile(fileobj=splitter, mode='wb', compresslevel=6) as packed:
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b''):
                packed.write(chunk)
        code = proc.wait(timeout=120)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        reader.join(timeout=5)
    if code != 0:
        detail = b''.join(errors).decode('utf-8', 'replace').strip()[-300:]
        raise RuntimeError(detail or f'pg_dump exited with {code}')
    splitter.finish()
    return splitter.parts, splitter.total


def _feed(on_part, data):
    splitter = PartSplitter(on_part)
    splitter.write(data)
    splitter.finish()
    return splitter.parts, splitter.total


def _part_sender(loop, bot, chat_id, name, caption):
    """on_part برای PartSplitter: هر بخش از همان نخِ کارگر، با event loopِ ربات فرستاده می‌شود."""
    sent = []

    def on_part(index, data, last):
        single = index == 1 and last
        filename = name if single else f'{name}.part{index}'
        text = (caption if single else f'{caption} — بخش {fa(index)}') + f' — {mb(len(data))} مگ'
        future = asyncio.run_coroutine_threadsafe(
            bot.send_document(chat_id, io.BytesIO(data), filename=filename, caption=text,
                              **_TIMEOUTS),
            loop)
        future.result(timeout=SEND_TIMEOUT)
        sent.append(filename)

    return on_part, sent


def _join_help(name, parts) -> str:
    names = ' + '.join(f'{name}.part{i}' for i in range(1, parts + 1))
    return (f'یکی‌کردنِ {name} (همهٔ بخش‌ها در یک پوشه):\n'
            f'ویندوز (CMD): copy /b {names} {name}\n'
            f'لینوکس/مک: cat {name}.part* > {name}')


def _short(exc) -> str:
    return (str(exc) or exc.__class__.__name__).strip().splitlines()[-1][:200]


async def run_backup(bot, chat_id):
    """هر دو بک‌آپ را می‌سازد و برای chat_id می‌فرستد؛ آخرش یک پیامِ خلاصه."""
    import site_api

    loop = asyncio.get_running_loop()
    started = time.monotonic()
    now = datetime.now(_TEHRAN)
    stamp = now.strftime('%Y-%m-%d-%H%M')
    try:
        from handlers.order_track import fa_datetime

        when = fa_datetime(now)
    except Exception:  # noqa: BLE001 — تاریخ فقط برای نمایش است
        when = fa(now.strftime('%Y-%m-%d %H:%M'))
    lines, joins, ok = [], [], True

    name = f'atomic-bot-{stamp}.sql.gz'
    on_part, sent = _part_sender(loop, bot, chat_id, name, f'🤖 دیتابیس ربات — {when}')
    try:
        parts, size = await asyncio.to_thread(dump_bot_database, on_part)
        lines.append(f'🤖 ربات: {mb(size)} مگ' + (f' در {fa(parts)} بخش' if parts > 1 else ''))
        if parts > 1:
            joins.append(_join_help(name, parts))
    except Exception as exc:  # noqa: BLE001
        log.exception('bot database backup failed')
        ok = False
        lines.append(f'❌ ربات: {_short(exc)}'
                     + ('\n   بخش‌هایی که از این بک‌آپ آمد ناقص است؛ پاکشان کن.' if sent else ''))

    result = await asyncio.to_thread(site_api.fetch_site_backup, chat_id)
    if result.get('ok'):
        name = result.get('filename') or f'atomicshop-{stamp}.sql.gz'
        on_part, sent = _part_sender(loop, bot, chat_id, name, f'🌐 دیتابیس سایت — {when}')
        try:
            parts, size = await asyncio.to_thread(_feed, on_part, result['data'])
            lines.append(f'🌐 سایت: {mb(size)} مگ' + (f' در {fa(parts)} بخش' if parts > 1 else ''))
            if parts > 1:
                joins.append(_join_help(name, parts))
        except Exception as exc:  # noqa: BLE001
            log.exception('site backup could not be sent')
            ok = False
            lines.append(f'❌ سایت: فرستادن نشد ({_short(exc)})')
    else:
        ok = False
        lines.append(f'❌ سایت: {result.get("error") or result.get("message") or "نرسید"}')

    seconds = int(time.monotonic() - started)
    head = (f'✅ بک‌آپ آماده شد — {when}' if ok else f'⚠️ بک‌آپ کامل نشد — {when}')
    text = '\n'.join([head + f' ({fa(seconds)} ثانیه)', *lines])
    if joins:
        text += '\n\n' + '\n\n'.join(joins)
    text += '\n\n⚠️ این فایل‌ها اطلاعاتِ همهٔ کاربران را دارند؛ جای امن نگهشان دار و برای کسی نفرست.'
    await bot.send_message(chat_id, text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton('💾 بک‌آپ دوباره', callback_data='bkp_home')],
        [InlineKeyboardButton('⚙️ تنظیمات', callback_data='admx_hub_system')],
    ]))
    return ok


async def _run_and_release(bot, chat_id):
    try:
        await run_backup(bot, chat_id)
    except Exception:  # noqa: BLE001
        log.exception('backup run failed')
        try:
            await bot.send_message(chat_id, '❌ بک‌آپ کامل نشد؛ جزئیات در لاگِ ربات است.')
        except Exception:  # noqa: BLE001
            pass
    finally:
        _STATE['running'] = False
        _STATE['last'] = time.monotonic()


def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ بگیر و همین‌جا بفرست', callback_data='bkp_go')],
        [InlineKeyboardButton('🔙 تنظیمات', callback_data='admx_hub_system')],
    ])


async def backup_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """bkp_home: توضیح و دکمهٔ شروع؛ bkp_go: ساختن و فرستادن در پس‌زمینه."""
    query = update.callback_query
    user = update.effective_user
    if user is None or not is_owner(user.id):
        await query.answer('فقط مالکِ ربات می‌تواند بک‌آپ بگیرد.', show_alert=True)
        return
    if query.data == 'bkp_home':
        await query.answer()
        try:
            await query.edit_message_text(HOME_TEXT, reply_markup=home_keyboard())
        except BadRequest as exc:
            if 'not modified' not in str(exc).lower():
                await query.message.reply_text(HOME_TEXT, reply_markup=home_keyboard())
        return
    if _STATE['running']:
        await query.answer('یک بک‌آپ در حال ساخت است؛ تا تمام شود صبر کن.', show_alert=True)
        return
    wait = COOLDOWN_SECONDS - (time.monotonic() - _STATE['last'])
    if _STATE['last'] and wait > 0:
        await query.answer(f'{fa(int(wait) + 1)} ثانیهٔ دیگر دوباره بزن.', show_alert=True)
        return
    _STATE['running'] = True
    try:
        await query.answer('شروع شد')
        try:
            await query.edit_message_text(
                '⏳ در حالِ گرفتنِ بک‌آپِ ربات و سایت…\n'
                'فایل‌ها یکی‌یکی همین‌جا می‌آیند و آخرش یک خلاصه.')
        except BadRequest:
            pass
        # در پس‌زمینه: ساختنِ بک‌آپ چند دقیقه است و بقیهٔ پیام‌های ربات نباید منتظر بمانند.
        ctx.application.create_task(_run_and_release(ctx.bot, user.id), update=update)
    except BaseException:
        _STATE['running'] = False
        raise
