"""بخش «🆕 رویداد جدید بازی» — ثبت آیتم تازه بازی و انتشار در چنل‌ها.

جریان مدیر: /admin ← «🆕 رویداد جدید بازی» ← ثبت رویداد
  ۱) عکس (قابل رد کردن)  ۲) توضیحات با ایموجی پریمیوم (قابل رد کردن)
  حداقل یکی از این دو اجباری است.
  ۳) پیش‌نمایش + انتخاب چنل‌هایی که ربات در آن‌ها ادمین است + انتشار
زیر هر پست: دکمه سبز «خرید جم در ربات» (مستقیم لیست جم) و دکمه آبی «خرید از سایت»
(atomicshop.ir) + دکمه دلخواه قرمز.

امکانات اضافه: آمار کلیک دکمه ربات، سنجاق در چنل، اطلاع به کاربران ربات،
به‌روزرسانی/حذف پست‌های منتشرشده، تشخیص خودکار چنل‌هایی که ربات ادمین شده.
هیچ منطق خرید، پرداخت یا کیف پول در این فایل نیست.
"""
import asyncio
import json
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import button_style  # noqa: F401 — دکمه‌های رنگی
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
    LinkPreviewOptions, MessageEntity, Update,
)
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

import appearance
import events_db as edb
import referral_db
from admin_notify import is_admin
from db import is_user_blocked, list_all_telegram_ids, list_forced_join_channels, log_admin_action

_LOG = logging.getLogger(__name__)

ST_PHOTO, ST_TEXT, ST_INPUT = 81, 82, 83
ROUTER_PATTERN = r'^gev_'
INPUT_PATTERN = r'^gevin_[a-z]+(?:_[a-z0-9]+)?$'
START_PATTERN = r'^/start evgem_\d+$'
FLOW_KEY = 'gev_flow'
PAGE_SIZE = 8
CAPTION_MAX = 1024
TEXT_MAX = 4096

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
_ADMIN_STATUSES = ('administrator', 'creator')
_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_TYPE_LABEL = {'channel': '📢', 'supergroup': '👥', 'group': '👥'}

SETTING_INPUTS = {
    'sbot': ('event_btn_bot', '🟢 متن دکمه خرید در ربات', 64),
    'ssite': ('event_btn_site', '🔵 متن دکمه خرید از سایت', 64),
    'surl': ('event_site_url', '🌐 آدرس سایت', 300),
}
EMOJI_INPUTS = {
    'ebot': ('event_btn_bot_emoji', 'دکمه خرید در ربات'),
    'esite': ('event_btn_site_emoji', 'دکمه خرید از سایت'),
}
DEFAULT_TOGGLES = {
    'pin': ('event_default_pin', '📌 سنجاق پست'),
    'ntf': ('event_default_notify', '🔔 اطلاع به کاربران ربات'),
    'site': ('event_default_site', '🌐 دکمه سایت'),
}


# ─── Helpers ────────────────────────────────────────────────────────────────────
def _btn(text, callback_data=None, style='primary', **kwargs):
    if callback_data is not None:
        kwargs['callback_data'] = callback_data
    return InlineKeyboardButton(text, style=style, **kwargs)


def utf16_len(text):
    return appearance.utf16_len(text or '')


def entities_to_json(entities):
    return json.dumps([e.to_dict() for e in (entities or [])], ensure_ascii=False)


def entities_from_json(raw):
    try:
        items = json.loads(raw or '[]')
    except (TypeError, ValueError):
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        try:
            entity = MessageEntity.de_json(item, None)
        except Exception:
            entity = None
        if entity is not None:
            out.append(entity)
    return out


def has_premium_emoji(event):
    return any(str(e.type) in ('custom_emoji', 'MessageEntityType.CUSTOM_EMOJI')
               for e in entities_from_json(event.get('body_entities')))


def valid_url(url):
    parsed = urlparse(str(url or '').strip())
    if parsed.scheme not in ('https', 'http') or parsed.username or not parsed.hostname:
        return False
    try:
        parsed.port
    except ValueError:
        return False
    return '.' in parsed.hostname and ' ' not in str(url)


def normalize_url(raw):
    url = str(raw or '').strip()
    if url and '://' not in url and not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:(?!\d)', url):
        url = 'https://' + url
    return url


def site_link(values, event_id):
    """لینک سایت با برچسب رهگیری کمپین (utm) برای دیدن بازدید هر رویداد."""
    url = normalize_url(values.get('event_site_url')) or edb.DEFAULT_SITE_URL
    if not valid_url(url):
        url = edb.DEFAULT_SITE_URL
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query.update(utm_source='telegram', utm_medium='channel', utm_campaign=f'event_{int(event_id)}')
    return urlunparse(parsed._replace(query=urlencode(query)))


def bot_link(bot_username, event_id):
    return f'https://t.me/{str(bot_username or "").lstrip("@")}?start=evgem_{int(event_id)}'


def event_markup(bot_username, event, values, icons=True):
    def icon(key):
        return (values.get(key) or None) if icons else None

    rows = [[_btn(
        values['event_btn_bot'], style='success', url=bot_link(bot_username, event['id']),
        icon_custom_emoji_id=icon('event_btn_bot_emoji'),
    )]]
    if event.get('show_site'):
        rows.append([_btn(
            values['event_btn_site'], style='primary', url=site_link(values, event['id']),
            icon_custom_emoji_id=icon('event_btn_site_emoji'),
        )])
    if event.get('extra_btn_text') and event.get('extra_btn_url'):
        rows.append([_btn(event['extra_btn_text'], style='danger', url=event['extra_btn_url'])])
    return InlineKeyboardMarkup(rows)


def _attempts(bot_username, event, values):
    """اول با ایموجی پریمیوم کامل؛ اگر تلگرام نپذیرفت، بدون ایموجی پریمیوم."""
    entities = entities_from_json(event.get('body_entities'))
    attempts = [(entities, event_markup(bot_username, event, values, icons=True))]
    plain = [e for e in entities if str(e.type) not in ('custom_emoji', 'MessageEntityType.CUSTOM_EMOJI')]
    if len(plain) != len(entities) or values.get('event_btn_bot_emoji') or values.get('event_btn_site_emoji'):
        attempts.append((plain, event_markup(bot_username, event, values, icons=False)))
    return attempts


async def send_event(bot, chat_id, event, values):
    """پست رویداد را می‌فرستد و (message, kind) برمی‌گرداند."""
    body = event.get('body') or ''
    photo = event.get('photo_file_id') or ''
    last_error = None
    for entities, markup in _attempts(bot.username, event, values):
        try:
            if photo:
                message = await bot.send_photo(
                    chat_id=chat_id, photo=photo, caption=body or None,
                    caption_entities=entities or None, reply_markup=markup,
                )
                return message, 'photo'
            message = await bot.send_message(
                chat_id=chat_id, text=body, entities=entities or None,
                reply_markup=markup, link_preview_options=_NO_PREVIEW,
            )
            return message, 'text'
        except BadRequest as exc:
            last_error = exc
    raise last_error


async def edit_post(bot, post, event, values):
    """پست منتشرشده را با محتوای فعلی رویداد به‌روز می‌کند: ok / same / mismatch / gone."""
    body = event.get('body') or ''
    photo = event.get('photo_file_id') or ''
    if (post['kind'] == 'photo') != bool(photo):
        return 'mismatch'
    last_error = None
    for entities, markup in _attempts(bot.username, event, values):
        try:
            if photo:
                result = await bot.edit_message_media(
                    chat_id=post['chat_id'], message_id=post['message_id'],
                    media=InputMediaPhoto(media=photo, caption=body or None,
                                          caption_entities=entities or None),
                    reply_markup=markup,
                )
            else:
                result = await bot.edit_message_text(
                    body, chat_id=post['chat_id'], message_id=post['message_id'],
                    entities=entities or None, reply_markup=markup,
                    link_preview_options=_NO_PREVIEW,
                )
            return 'ok' if result is not None else 'same'
        except BadRequest as exc:
            text = str(exc).lower()
            if 'not modified' in text:
                return 'same'
            if 'not found' in text or "can't be edited" in text:
                return 'gone'
            last_error = exc
    raise last_error


def selected_channel_ids(event, channels):
    ids = {c['id'] for c in channels}
    if event.get('target_chats') is None:
        return ids
    return ids & set(event['target_chats'])


def parse_chat_ref(message):
    """شناسه چنل از پیام فورواردی، @username، لینک t.me یا شناسه عددی."""
    origin = getattr(message, 'forward_origin', None)
    chat = getattr(origin, 'chat', None) if origin is not None else None
    if chat is None:
        chat = getattr(message, 'forward_from_chat', None)
    if chat is not None and getattr(chat, 'id', None):
        return chat.id
    raw = str(getattr(message, 'text', '') or '').translate(_DIGITS).strip()
    if not raw:
        return None
    match = re.fullmatch(r'(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,})/?(?:\d+)?', raw)
    if match:
        return '@' + match.group(1)
    if re.fullmatch(r'@?[A-Za-z][A-Za-z0-9_]{3,}', raw):
        return '@' + raw.lstrip('@')
    if re.fullmatch(r'-?\d{5,20}', raw):
        return int(raw)
    return None


async def check_chat(bot, chat_ref):
    """دسترسی ربات را بررسی و در لیست چنل‌ها ذخیره می‌کند؛ (channel, error)."""
    try:
        chat = await bot.get_chat(chat_ref)
        member = await bot.get_chat_member(chat.id, bot.id)
    except (BadRequest, Forbidden) as exc:
        return None, f'ربات به این چت دسترسی ندارد ({exc}).'
    if str(chat.type) not in ('channel', 'supergroup', 'group'):
        return None, 'فقط چنل یا گروه قابل ثبت است.'
    status = str(member.status)
    if status not in _ADMIN_STATUSES:
        await asyncio.to_thread(edb.deactivate_channel, chat.id)
        return None, 'ربات در این چت ادمین نیست؛ اول ربات را ادمین کن.'
    can_post = (
        str(chat.type) != 'channel' or status == 'creator'
        or bool(getattr(member, 'can_post_messages', False))
    )
    channel = await asyncio.to_thread(
        edb.upsert_channel, chat.id, chat.title or chat.username or str(chat.id),
        chat.username or '', str(chat.type), can_post, True,
    )
    if not can_post:
        return channel, 'ربات ادمین است ولی دسترسی «ارسال پست» ندارد.'
    return channel, ''


async def refresh_channels(bot):
    """بررسی دوباره همه چنل‌های شناخته‌شده + چنل‌های جوین اجباری."""
    refs = {}
    for channel in await asyncio.to_thread(edb.list_channels, False):
        refs[channel['chat_id']] = int(channel['chat_id']) if channel['chat_id'].lstrip('-').isdigit() \
            else channel['chat_id']
    try:
        for row in await asyncio.to_thread(list_forced_join_channels, False):
            refs.setdefault(str(row[1]), row[1])
    except Exception:
        _LOG.debug('Forced join channels unavailable', exc_info=True)
    try:
        from handlers.admin_extended import _KNOWN_ADMIN_CHANNELS
        for info in list(_KNOWN_ADMIN_CHANNELS.values()):
            refs.setdefault(str(info.get('raw_id')), info.get('raw_id'))
    except Exception:
        pass
    ok = 0
    for key, ref in refs.items():
        try:
            channel, _error = await check_chat(bot, ref)
            ok += bool(channel and channel['can_post'])
        except Exception:
            _LOG.info('Channel refresh failed for %s', key, exc_info=True)
    return ok


def _flow(ctx):
    return ctx.user_data.get(FLOW_KEY) or {}


async def _guard(update):
    uid = update.effective_user.id if update.effective_user else None
    if await asyncio.to_thread(is_admin, uid):
        return True
    if update.callback_query:
        await update.callback_query.answer('دسترسی ندارید.', show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text('دسترسی ندارید.')
    return False


async def _edit(query, text, rows):
    markup = InlineKeyboardMarkup(rows) if rows else None
    try:
        if getattr(query.message, 'text', None) is not None:
            await query.edit_message_text(text[:4096], reply_markup=markup,
                                          link_preview_options=_NO_PREVIEW)
            return
    except BadRequest as exc:
        if 'not modified' in str(exc).lower():
            return
    await query.get_bot().send_message(
        chat_id=query.from_user.id, text=text[:4096], reply_markup=markup,
        link_preview_options=_NO_PREVIEW,
    )


def _state(flag):
    return 'روشن ✅' if flag else 'خاموش'


# ─── Admin screens ──────────────────────────────────────────────────────────────
def home_view(events_total, channels):
    text = (
        '🆕 رویدادهای جدید بازی\n'
        '━━━━━━━━━━━━━━━\n'
        'آیتم یا رویداد تازه‌ای به بازی اضافه شده؟ ثبتش کن و با دکمه‌های خرید جم\n'
        'در چنل‌هایی که ربات در آن‌ها ادمین است منتشرش کن.\n\n'
        f'📋 رویدادهای ثبت‌شده: {events_total:,}\n'
        f'📢 چنل‌های آماده انتشار: {len(channels):,}\n\n'
        '💡 چنل تازه: ربات را در چنل ادمین کن (با دسترسی ارسال پست)؛ خودکار اضافه می‌شود.'
    )
    rows = [
        [_btn('🆕 ثبت رویداد جدید', 'gevin_new', 'success')],
        [_btn('📋 رویدادهای ثبت‌شده', 'gev_list_0')],
        [_btn('📢 چنل‌های ربات', 'gev_chans'), _btn('⚙️ دکمه‌ها و تنظیمات', 'gev_set')],
        [_btn('🏠 منوی اصلی', 'adm_home')],
    ]
    return text, rows


def panel_view(event, channels, values):
    eid = event['id']
    selected = selected_channel_ids(event, channels)
    body = event['body']
    status = '✅ منتشر شده' if event['status'] == 'published' else '📝 پیش‌نویس'
    lines = [
        f'🆕 رویداد #{eid} — {status}',
        '━━━━━━━━━━━━━━━',
        f'عنوان: {edb.event_title(event, 60)}',
        f'🖼 عکس: {"دارد ✅" if event["photo_file_id"] else "ندارد"}',
        f'📝 توضیحات: {f"{utf16_len(body):,} کاراکتر" if body else "ندارد"}'
        + (' · ⭐ ایموجی پریمیوم' if has_premium_emoji(event) else ''),
        f'🔘 دکمه دلخواه: {event["extra_btn_text"] or "ندارد"}',
        f'📢 چنل‌های انتخابی: {len(selected)} از {len(channels)}',
        f'📌 سنجاق: {_state(event["pin"])} · 🔔 اطلاع به کاربران: {_state(event["notify_users"])}'
        + (' (ارسال شد)' if event['notified_at'] else ''),
        f'🌐 دکمه سایت: {_state(event["show_site"])}',
        '',
        f'📊 کلیک دکمه ربات: {event["clicks"]:,} (کاربر یکتا: {event["unique_clicks"]:,})',
        f'📨 پست‌های منتشرشده: {event["posts"]:,}',
    ]
    if event['published_at']:
        lines.append(f'🕒 انتشار: {referral_db.format_dt(event["published_at"])}')
    if not channels:
        lines.append('\n⚠️ هنوز چنلی ثبت نشده؛ ربات را در چنل ادمین کن یا از «انتخاب چنل‌ها» اضافه کن.')
    media_row = [_btn('🖼 تغییر عکس' if event['photo_file_id'] else '🖼 افزودن عکس', f'gevin_photo_{eid}')]
    if event['photo_file_id'] and body:
        media_row.append(_btn('🗑 حذف عکس', f'gev_rmphoto_{eid}', 'danger'))
    text_row = [_btn('📝 تغییر توضیحات' if body else '📝 افزودن توضیحات', f'gevin_text_{eid}')]
    if body and event['photo_file_id']:
        text_row.append(_btn('🗑 حذف توضیحات', f'gev_rmtext_{eid}', 'danger'))
    extra_row = [_btn('🔘 دکمه دلخواه', f'gevin_extra_{eid}')]
    if event['extra_btn_text']:
        extra_row.append(_btn('🗑 حذف دکمه دلخواه', f'gev_rmextra_{eid}', 'danger'))
    rows = [
        [_btn('👀 پیش‌نمایش پست', f'gev_prev_{eid}')],
        media_row,
        text_row,
        extra_row,
        [_btn(f'📢 انتخاب چنل‌ها ({len(selected)})', f'gev_ch_{eid}')],
        [_btn(f'📌 سنجاق: {_state(event["pin"])}', f'gev_tpin_{eid}'),
         _btn(f'🔔 اطلاع کاربران: {_state(event["notify_users"])}', f'gev_tntf_{eid}')],
        [_btn(f'🌐 دکمه سایت: {_state(event["show_site"])}', f'gev_tsite_{eid}')],
        [_btn('🚀 انتشار در چنل‌ها', f'gev_pub_{eid}', 'success')],
    ]
    if event['posts']:
        rows.append([_btn('🔄 به‌روزرسانی پست‌های منتشرشده', f'gev_sync_{eid}'),
                     _btn('🗑 حذف از چنل‌ها', f'gev_unpub_{eid}', 'danger')])
    rows.append([_btn('🗑 حذف رویداد', f'gev_del_{eid}', 'danger')])
    rows.append([_btn('🔙 رویدادها', 'gev_list_0'), _btn('🆕 پنل رویداد', 'gev_home')])
    return '\n'.join(lines), rows


def channel_picker_view(event, channels):
    eid = event['id']
    selected = selected_channel_ids(event, channels)
    lines = [
        f'📢 چنل‌های انتشار رویداد #{eid}',
        '━━━━━━━━━━━━━━━',
        'روی هر چنل بزن تا انتخاب/لغو شود.',
    ]
    if not channels:
        lines.append('\nهنوز چنلی ثبت نشده. ربات را در چنل ادمین کن (ارسال پست)، '
                     'یا «➕ افزودن چنل» را بزن و یک پست از چنل فوروارد کن.')
    rows = []
    for channel in channels:
        mark = '✅' if channel['id'] in selected else '⬜️'
        icon = _TYPE_LABEL.get(channel['chat_type'], '📢')
        rows.append([_btn(f'{mark} {icon} {channel["title"][:40]}',
                          f'gev_cht_{eid}_{channel["id"]}',
                          'success' if channel['id'] in selected else 'primary')])
    if channels:
        rows.append([_btn('☑️ همه', f'gev_chall_{eid}'), _btn('⬜️ هیچ‌کدام', f'gev_chnone_{eid}')])
    rows.append([_btn('➕ افزودن چنل', f'gevin_addch_{eid}'),
                 _btn('🔄 بررسی دسترسی', f'gev_chref_{eid}')])
    rows.append([_btn('🔙 رویداد', f'gev_ev_{eid}')])
    return '\n'.join(lines), rows


def channels_view(channels):
    lines = ['📢 چنل‌های ربات برای انتشار رویداد', '━━━━━━━━━━━━━━━']
    rows = []
    if not channels:
        lines.append('هنوز چنلی ثبت نشده.')
    for channel in channels:
        state = '✅' if channel['active'] and channel['can_post'] else (
            '⚠️ بدون دسترسی ارسال' if channel['active'] else '❌ ربات ادمین نیست')
        handle = f'@{channel["username"]}' if channel['username'] else channel['chat_id']
        lines.append(f'{_TYPE_LABEL.get(channel["chat_type"], "📢")} {channel["title"]} · {handle} · {state}')
        rows.append([_btn(f'🗑 {channel["title"][:40]}', f'gev_chdel_{channel["id"]}', 'danger')])
    lines.extend([
        '',
        '➕ چنل جدید: ربات را در چنل ادمین کن تا خودکار اضافه شود،',
        'یا «افزودن چنل» را بزن و یک پست از چنل فوروارد کن یا @username / شناسه -100… بفرست.',
    ])
    rows.append([_btn('➕ افزودن چنل', 'gevin_addch', 'success'),
                 _btn('🔄 بررسی دسترسی همه', 'gev_chansref')])
    rows.append([_btn('🔙 پنل رویداد', 'gev_home')])
    return '\n'.join(lines), rows


def settings_view(values):
    text = (
        '⚙️ دکمه‌ها و تنظیمات رویداد\n'
        '━━━━━━━━━━━━━━━\n'
        f'🟢 دکمه ربات: {values["event_btn_bot"]}\n'
        f'   ⭐ ایموجی پریمیوم: {"دارد" if values["event_btn_bot_emoji"] else "ندارد"}\n'
        f'🔵 دکمه سایت: {values["event_btn_site"]}\n'
        f'   ⭐ ایموجی پریمیوم: {"دارد" if values["event_btn_site_emoji"] else "ندارد"}\n'
        f'🌐 آدرس سایت: {values["event_site_url"]}\n\n'
        'پیش‌فرض رویدادهای جدید:\n'
        + '\n'.join(f'{title}: {_state(edb.is_on(values, key))}'
                    for key, title in DEFAULT_TOGGLES.values())
        + '\n\n💡 دکمه ربات مستقیم لیست «جم با آیدی» را باز می‌کند و کلیک‌هایش شمرده می‌شود.\n'
        'به لینک سایت برچسب utm اضافه می‌شود تا بازدید هر رویداد در آمار سایت دیده شود.'
    )
    rows = [
        [_btn('✏️ متن دکمه ربات', 'gevin_set_sbot'), _btn('⭐ ایموجی دکمه ربات', 'gevin_emoji_ebot')],
        [_btn('✏️ متن دکمه سایت', 'gevin_set_ssite'), _btn('⭐ ایموجی دکمه سایت', 'gevin_emoji_esite')],
        [_btn('🌐 آدرس سایت', 'gevin_set_surl')],
    ]
    rows.extend(
        [_btn(f'{title}: {_state(edb.is_on(values, key))}', f'gev_tdef_{code}')]
        for code, (key, title) in DEFAULT_TOGGLES.items()
    )
    rows.append([_btn('🔙 پنل رویداد', 'gev_home')])
    return text, rows


def list_view(events, total, page):
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    lines = [f'📋 رویدادهای ثبت‌شده — صفحه {page + 1} از {pages}', '━━━━━━━━━━━━━━━']
    rows = []
    for event in events:
        mark = '✅' if event['status'] == 'published' else '📝'
        lines.append(f'{mark} #{event["id"]} · {edb.event_title(event)} · کلیک {event["clicks"]:,}')
        rows.append([_btn(f'{mark} #{event["id"]} · {edb.event_title(event, 30)}', f'gev_ev_{event["id"]}')])
    if not events:
        lines.append('هنوز رویدادی ثبت نشده.')
    nav = []
    if page > 0:
        nav.append(_btn('◀️ قبلی', f'gev_list_{page - 1}'))
    if page + 1 < pages:
        nav.append(_btn('بعدی ▶️', f'gev_list_{page + 1}'))
    if nav:
        rows.append(nav)
    rows.append([_btn('🆕 ثبت رویداد جدید', 'gevin_new', 'success')])
    rows.append([_btn('🔙 پنل رویداد', 'gev_home')])
    return '\n'.join(lines), rows


async def show_home(query):
    _events, total = await asyncio.to_thread(edb.list_events, 1, 0)
    channels = await asyncio.to_thread(edb.list_channels, True)
    text, rows = home_view(total, channels)
    await _edit(query, text, rows)


async def show_panel(query, event_id, notice=''):
    event = await asyncio.to_thread(edb.get_event, event_id)
    if not event:
        await _edit(query, 'رویداد پیدا نشد.', [[_btn('🔙 رویدادها', 'gev_list_0')]])
        return
    channels = await asyncio.to_thread(edb.list_channels, True)
    values = await asyncio.to_thread(edb.settings)
    text, rows = panel_view(event, channels, values)
    await _edit(query, f'{notice}\n\n{text}' if notice else text, rows)


async def send_preview(bot, chat_id, event_id, notice=''):
    """خودِ پست (دقیقاً همان‌طور که در چنل می‌رود) + پنل کنترل."""
    event = await asyncio.to_thread(edb.get_event, event_id)
    if not event:
        return
    values = await asyncio.to_thread(edb.settings)
    channels = await asyncio.to_thread(edb.list_channels, True)
    await bot.send_message(chat_id=chat_id, text='👀 پیش‌نمایش پست رویداد 👇')
    try:
        await send_event(bot, chat_id, event, values)
    except BadRequest as exc:
        await bot.send_message(chat_id=chat_id, text=f'❌ پیش‌نمایش ارسال نشد: {exc}')
    text, rows = panel_view(event, channels, values)
    await bot.send_message(
        chat_id=chat_id, text=f'{notice}\n\n{text}' if notice else text,
        reply_markup=InlineKeyboardMarkup(rows), link_preview_options=_NO_PREVIEW,
    )


# ─── Publishing ─────────────────────────────────────────────────────────────────
async def publish_event(bot, event_id):
    event = await asyncio.to_thread(edb.get_event, event_id)
    values = await asyncio.to_thread(edb.settings)
    channels = await asyncio.to_thread(edb.list_channels, True)
    selected = selected_channel_ids(event, channels)
    posted = {p['chat_id'] for p in await asyncio.to_thread(edb.list_posts, event_id)}
    sent, skipped, failed = [], [], []
    for channel in channels:
        if channel['id'] not in selected:
            continue
        if channel['chat_id'] in posted:
            skipped.append(channel['title'])
            continue
        try:
            message, kind = await send_event(bot, int(channel['chat_id']), event, values)
            await asyncio.to_thread(edb.record_post, event_id, channel['chat_id'], message.message_id, kind)
            sent.append(channel['title'])
            if event['pin']:
                try:
                    await bot.pin_chat_message(chat_id=int(channel['chat_id']),
                                               message_id=message.message_id,
                                               disable_notification=True)
                except (BadRequest, Forbidden):
                    failed.append(f'{channel["title"]} (سنجاق نشد)')
        except (BadRequest, Forbidden) as exc:
            failed.append(f'{channel["title"]}: {exc}')
            if isinstance(exc, Forbidden):
                await asyncio.to_thread(edb.deactivate_channel, channel['chat_id'])
        await asyncio.sleep(0.1)
    if sent:
        await asyncio.to_thread(edb.mark_published, event_id)
    return sent, skipped, failed


async def notify_bot_users(bot, event_id, admin_chat_id):
    """پست رویداد برای همه کاربران ربات (یک‌بار برای هر رویداد)."""
    try:
        event = await asyncio.to_thread(edb.get_event, event_id)
        values = await asyncio.to_thread(edb.settings)
        ids = await asyncio.to_thread(list_all_telegram_ids)
        sent = failed = 0
        for index, tg in enumerate(ids, start=1):
            try:
                await send_event(bot, int(tg), event, values)
                sent += 1
            except Exception:
                failed += 1
            if index % 25 == 0:
                await asyncio.sleep(1)
        await asyncio.to_thread(log_admin_action, admin_chat_id, 'event_notify', 'event',
                                event_id, f'sent={sent} failed={failed}')
        await bot.send_message(
            chat_id=admin_chat_id,
            text=f'🔔 اطلاع رویداد #{event_id} به کاربران ربات تمام شد.\nموفق: {sent:,} · ناموفق: {failed:,}',
            reply_markup=InlineKeyboardMarkup([[_btn('🆕 رویداد', f'gev_ev_{event_id}')]]),
        )
    except Exception:
        _LOG.exception('Event notify failed event=%s', event_id)


async def sync_posts(bot, event_id):
    event = await asyncio.to_thread(edb.get_event, event_id)
    values = await asyncio.to_thread(edb.settings)
    counts = {'ok': 0, 'same': 0, 'mismatch': 0, 'gone': 0, 'failed': 0}
    for post in await asyncio.to_thread(edb.list_posts, event_id):
        try:
            result = await edit_post(bot, post, event, values)
        except (BadRequest, Forbidden):
            result = 'failed'
        counts[result] += 1
        if result == 'gone':
            await asyncio.to_thread(edb.delete_post, post['id'])
    await asyncio.to_thread(edb.reset_status_if_unposted, event_id)
    return counts


async def unpublish(bot, event_id):
    removed = failed = 0
    for post in await asyncio.to_thread(edb.list_posts, event_id):
        try:
            await bot.delete_message(chat_id=int(post['chat_id']), message_id=post['message_id'])
            ok = True
        except BadRequest as exc:
            ok = 'not found' in str(exc).lower()
        except Forbidden:
            ok = False
        if ok:
            await asyncio.to_thread(edb.delete_post, post['id'])
            removed += 1
        else:
            failed += 1
    await asyncio.to_thread(edb.reset_status_if_unposted, event_id)
    return removed, failed


# ─── Router ─────────────────────────────────────────────────────────────────────
async def event_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or not await _guard(update):
        return
    data = query.data or ''
    parts = data.split('_')
    name = parts[1] if len(parts) > 1 else ''
    nums = [int(p) for p in parts[2:] if p.isdigit()]
    eid = nums[0] if nums else None
    uid = query.from_user.id
    if name not in ('pubok', 'prev', 'sync', 'chref', 'chansref', 'unpubok'):
        await query.answer()

    if name == 'home':
        await show_home(query)
    elif name == 'list':
        page = max(0, eid or 0)
        events, total = await asyncio.to_thread(edb.list_events, PAGE_SIZE, page * PAGE_SIZE)
        text, rows = list_view(events, total, page)
        await _edit(query, text, rows)
    elif name == 'ev' and eid:
        await show_panel(query, eid)
    elif name == 'prev' and eid:
        await query.answer('پیش‌نمایش ارسال شد 👇')
        await send_preview(ctx.bot, uid, eid)
    elif name in ('rmphoto', 'rmtext', 'rmextra') and eid:
        event = await asyncio.to_thread(edb.get_event, eid)
        if not event:
            return
        if name == 'rmphoto':
            if not event['body']:
                await query.answer('بدون عکس، توضیحات لازم است.', show_alert=True)
                return
            await asyncio.to_thread(edb.update_event, eid, photo_file_id='')
        elif name == 'rmtext':
            if not event['photo_file_id']:
                await query.answer('بدون توضیحات، عکس لازم است.', show_alert=True)
                return
            await asyncio.to_thread(edb.update_event, eid, body='', body_entities='[]')
        else:
            await asyncio.to_thread(edb.update_event, eid, extra_btn_text='', extra_btn_url='')
        notice = '✅ ذخیره شد.'
        if event['posts'] and name != 'rmextra':
            notice += ' ⚠️ نوع پست عوض شد؛ پست‌های قبلی را «حذف از چنل‌ها» و دوباره منتشر کن.'
        await show_panel(query, eid, notice)
    elif name in ('tpin', 'tntf', 'tsite') and eid:
        event = await asyncio.to_thread(edb.get_event, eid)
        field = {'tpin': 'pin', 'tntf': 'notify_users', 'tsite': 'show_site'}[name]
        await asyncio.to_thread(edb.update_event, eid, **{field: not event[field]})
        await show_panel(query, eid)
    elif name == 'ch' and eid:
        event = await asyncio.to_thread(edb.get_event, eid)
        channels = await asyncio.to_thread(edb.list_channels, True)
        if not channels:
            await refresh_channels(ctx.bot)
            channels = await asyncio.to_thread(edb.list_channels, True)
        text, rows = channel_picker_view(event, channels)
        await _edit(query, text, rows)
    elif name in ('cht', 'chall', 'chnone') and eid:
        event = await asyncio.to_thread(edb.get_event, eid)
        channels = await asyncio.to_thread(edb.list_channels, True)
        selected = selected_channel_ids(event, channels)
        if name == 'cht' and len(nums) > 1:
            selected ^= {nums[1]}
        elif name == 'chall':
            selected = {c['id'] for c in channels}
        else:
            selected = set()
        target = None if selected == {c['id'] for c in channels} else selected
        event = await asyncio.to_thread(edb.update_event, eid, target_chats=target)
        text, rows = channel_picker_view(event, channels)
        await _edit(query, text, rows)
    elif name == 'chref' and eid:
        await query.answer('در حال بررسی دسترسی چنل‌ها…')
        await refresh_channels(ctx.bot)
        event = await asyncio.to_thread(edb.get_event, eid)
        channels = await asyncio.to_thread(edb.list_channels, True)
        text, rows = channel_picker_view(event, channels)
        await _edit(query, text, rows)
    elif name == 'pub' and eid:
        event = await asyncio.to_thread(edb.get_event, eid)
        channels = await asyncio.to_thread(edb.list_channels, True)
        selected = [c for c in channels if c['id'] in selected_channel_ids(event, channels)]
        if not selected:
            await _edit(query, '⚠️ هیچ چنلی انتخاب نشده.', [
                [_btn('📢 انتخاب چنل‌ها', f'gev_ch_{eid}')], [_btn('🔙 رویداد', f'gev_ev_{eid}')],
            ])
            return
        names = '\n'.join(f'• {c["title"]}' for c in selected)
        extra = ''
        if event['notify_users'] and not event['notified_at']:
            extra = '\n🔔 بعد از انتشار، پست برای همه کاربران ربات هم ارسال می‌شود.'
        await _edit(query, (
            f'🚀 رویداد #{eid} در این چنل‌ها منتشر می‌شود:\n{names}\n'
            f'📌 سنجاق: {_state(event["pin"])}{extra}\n\n'
            'چنل‌هایی که قبلاً این رویداد را گرفته‌اند تکراری ارسال نمی‌شوند. منتشر شود؟'
        ), [
            [_btn('✅ بله، منتشر کن', f'gev_pubok_{eid}', 'success')],
            [_btn('👀 پیش‌نمایش', f'gev_prev_{eid}'), _btn('🔙 انصراف', f'gev_ev_{eid}')],
        ])
    elif name == 'pubok' and eid:
        await query.answer('در حال انتشار…')
        sent, skipped, failed = await publish_event(ctx.bot, eid)
        await asyncio.to_thread(log_admin_action, uid, 'event_published', 'event', eid,
                                f'sent={len(sent)} failed={len(failed)}')
        lines = [f'🚀 نتیجه انتشار رویداد #{eid}']
        if sent:
            lines.append('✅ ارسال شد: ' + '، '.join(sent))
        if skipped:
            lines.append('⏭ قبلاً ارسال شده بود: ' + '، '.join(skipped))
        if failed:
            lines.append('❌ خطا:\n' + '\n'.join(failed))
        event = await asyncio.to_thread(edb.get_event, eid)
        if sent and event['notify_users'] and await asyncio.to_thread(edb.claim_notify, eid):
            ctx.application.create_task(notify_bot_users(ctx.bot, eid, uid))
            lines.append('🔔 ارسال برای کاربران ربات شروع شد؛ نتیجه را جداگانه می‌فرستم.')
        await show_panel(query, eid, '\n'.join(lines))
    elif name == 'sync' and eid:
        await query.answer('در حال به‌روزرسانی پست‌ها…')
        counts = await sync_posts(ctx.bot, eid)
        notice = (
            f'🔄 به‌روزرسانی: {counts["ok"]} پست تغییر کرد · {counts["same"]} بدون تغییر'
            + (f' · {counts["gone"]} پست پاک شده بود' if counts['gone'] else '')
            + (f' · {counts["failed"]} خطا' if counts['failed'] else '')
            + (f'\n⚠️ {counts["mismatch"]} پست نوع دیگری دارد (عکس/متن)؛ حذف و دوباره منتشر کن.'
               if counts['mismatch'] else '')
        )
        await show_panel(query, eid, notice)
    elif name == 'unpub' and eid:
        posts = await asyncio.to_thread(edb.list_posts, eid)
        names = '\n'.join(f'• {p["title"] or p["chat_id"]}' for p in posts)
        await _edit(query, f'🗑 پست رویداد #{eid} از این چنل‌ها پاک می‌شود:\n{names}\n\nمطمئنی؟', [
            [_btn('✅ بله، حذف از چنل‌ها', f'gev_unpubok_{eid}', 'danger')],
            [_btn('🔙 انصراف', f'gev_ev_{eid}')],
        ])
    elif name == 'unpubok' and eid:
        await query.answer('در حال حذف…')
        removed, failed = await unpublish(ctx.bot, eid)
        await asyncio.to_thread(log_admin_action, uid, 'event_unpublished', 'event', eid,
                                f'removed={removed} failed={failed}')
        await show_panel(query, eid, f'🗑 {removed} پست حذف شد' + (f' · {failed} خطا' if failed else ''))
    elif name == 'del' and eid:
        await _edit(query, (
            f'🗑 رویداد #{eid} و آمارش از پنل حذف می‌شود.\n'
            'پست‌های منتشرشده در چنل‌ها می‌مانند (برای پاک کردن آن‌ها اول «حذف از چنل‌ها» را بزن). مطمئنی؟'
        ), [
            [_btn('✅ بله، حذف رویداد', f'gev_delok_{eid}', 'danger')],
            [_btn('🔙 انصراف', f'gev_ev_{eid}')],
        ])
    elif name == 'delok' and eid:
        await asyncio.to_thread(edb.delete_event, eid)
        await asyncio.to_thread(log_admin_action, uid, 'event_deleted', 'event', eid, '')
        events, total = await asyncio.to_thread(edb.list_events, PAGE_SIZE, 0)
        text, rows = list_view(events, total, 0)
        await _edit(query, f'✅ رویداد #{eid} حذف شد.\n\n{text}', rows)
    elif name in ('chans', 'chansref'):
        if name == 'chansref':
            await query.answer('در حال بررسی دسترسی چنل‌ها…')
            await refresh_channels(ctx.bot)
        text, rows = channels_view(await asyncio.to_thread(edb.list_channels, False))
        await _edit(query, text, rows)
    elif name == 'chdel' and eid:
        await asyncio.to_thread(edb.remove_channel, eid)
        text, rows = channels_view(await asyncio.to_thread(edb.list_channels, False))
        await _edit(query, '✅ چنل از لیست انتشار حذف شد (ربات از چنل خارج نمی‌شود).\n\n' + text, rows)
    elif name == 'set':
        text, rows = settings_view(await asyncio.to_thread(edb.settings, True))
        await _edit(query, text, rows)
    elif name == 'tdef' and len(parts) > 2 and parts[2] in DEFAULT_TOGGLES:
        key = DEFAULT_TOGGLES[parts[2]][0]
        values = await asyncio.to_thread(edb.settings, True)
        await asyncio.to_thread(edb.put, key, '0' if edb.is_on(values, key) else '1')
        text, rows = settings_view(await asyncio.to_thread(edb.settings, True))
        await _edit(query, text, rows)


# ─── Channel auto-detection ─────────────────────────────────────────────────────
async def track_bot_membership(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ربات در چنل/گروه ادمین شد یا کنار گذاشته شد → لیست انتشار به‌روز می‌شود."""
    member_update = update.my_chat_member
    if member_update is None:
        return
    try:
        chat = member_update.chat
        if str(chat.type) not in ('channel', 'supergroup', 'group'):
            return
        new = member_update.new_chat_member
        status = str(new.status)
        if status in _ADMIN_STATUSES:
            can_post = (
                str(chat.type) != 'channel' or status == 'creator'
                or bool(getattr(new, 'can_post_messages', False))
            )
            await asyncio.to_thread(
                edb.upsert_channel, chat.id, chat.title or chat.username or str(chat.id),
                chat.username or '', str(chat.type), can_post, True,
            )
        else:
            await asyncio.to_thread(edb.deactivate_channel, chat.id)
    except Exception:
        _LOG.exception('Event channel tracking failed')


# ─── Deep link from channel posts ───────────────────────────────────────────────
async def event_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/start evgem_<id>: خوش‌آمد معمول ربات + لیست جم با آیدی + شمارش کلیک."""
    from handlers.referral import _send_gem_list
    from handlers.start import start_handler

    await start_handler(update, ctx)
    user = update.effective_user
    if user is None:
        return
    try:
        if await asyncio.to_thread(is_user_blocked, user.id) and not await asyncio.to_thread(is_admin, user.id):
            return
        event_id = int(ctx.args[0].split('_', 1)[1]) if ctx.args else None
        if event_id:
            try:
                await asyncio.to_thread(edb.record_click, event_id, user.id)
            except Exception:
                _LOG.info('Event click not recorded', exc_info=True)
        ctx.user_data['gems_page'] = 1
        await _send_gem_list(ctx.bot, user.id)
    except Exception:
        _LOG.exception('Event deep link failed')


# ─── Conversation (photo / text / inputs) ───────────────────────────────────────
def _photo_prompt(flow):
    rows = []
    if flow.get('mode') == 'new':
        rows.append([_btn('⏭ رد کردن عکس', 'gevs_skipphoto', 'danger')])
    rows.append([_btn('❌ لغو', 'gevs_cancel', 'danger')])
    text = (
        f'🖼 رویداد #{flow["id"]} — مرحله ۱ از ۲\n\n'
        'عکس آیتم یا رویداد جدید را بفرست.\n'
        '• می‌توانی عکس را همراه کپشن (با ایموجی پریمیوم) بفرستی تا مرحله توضیحات هم پر شود.\n'
        '• عکس نداری؟ «رد کردن عکس» را بزن یا مستقیم متن توضیحات را بفرست.'
        if flow.get('mode') == 'new' else
        f'🖼 عکس جدید رویداد #{flow["id"]} را بفرست.'
    )
    return text, InlineKeyboardMarkup(rows)


def _text_prompt(flow, has_photo):
    rows = []
    if flow.get('mode') == 'new' and has_photo:
        rows.append([_btn('⏭ رد کردن توضیحات', 'gevs_skiptext', 'danger')])
    rows.append([_btn('❌ لغو', 'gevs_cancel', 'danger')])
    limit = CAPTION_MAX if has_photo else TEXT_MAX
    step = 'مرحله ۲ از ۲ — ' if flow.get('mode') == 'new' else ''
    text = (
        f'📝 رویداد #{flow["id"]} — {step}توضیحات\n\n'
        'متن توضیحات را بفرست. ایموجی پریمیوم، بولد، لینک و… همان‌طور که می‌فرستی در پست می‌آید.\n'
        f'حداکثر {limit:,} کاراکتر' + (' (محدودیت کپشن عکس در تلگرام).' if has_photo else '.')
    )
    if flow.get('mode') == 'new' and not has_photo:
        text += '\n\n⚠️ عکس رد شده؛ توضیحات اجباری است.'
    return text, InlineKeyboardMarkup(rows)


async def _finish(update, ctx, event_id, notice='✅ رویداد ذخیره شد.'):
    ctx.user_data.pop(FLOW_KEY, None)
    await send_preview(ctx.bot, update.effective_user.id, event_id, notice)
    return ConversationHandler.END


async def _menu_interrupt(update, ctx):
    text = getattr(update.message, 'text', None)
    if text and appearance.menu_action(text):
        ctx.user_data.pop(FLOW_KEY, None)
        await update.message.reply_text('ثبت رویداد متوقف شد؛ دوباره دکمه منو را بزن.')
        return True
    return False


async def input_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _guard(update):
        return ConversationHandler.END
    await query.answer()
    action, _sep, arg = query.data[len('gevin_'):].partition('_')
    uid = query.from_user.id
    if action == 'new':
        values = await asyncio.to_thread(edb.settings, True)
        event_id = await asyncio.to_thread(edb.create_event, uid, values)
        flow = {'id': event_id, 'mode': 'new'}
        ctx.user_data[FLOW_KEY] = flow
        text, markup = _photo_prompt(flow)
        await _edit(query, text, markup.inline_keyboard)
        return ST_PHOTO
    if action in ('photo', 'text') and arg.isdigit():
        event = await asyncio.to_thread(edb.get_event, int(arg))
        if not event:
            await query.answer('رویداد پیدا نشد.', show_alert=True)
            return ConversationHandler.END
        flow = {'id': event['id'], 'mode': 'edit'}
        ctx.user_data[FLOW_KEY] = flow
        if action == 'photo':
            text, markup = _photo_prompt(flow)
            await _edit(query, text, markup.inline_keyboard)
            return ST_PHOTO
        text, markup = _text_prompt(flow, bool(event['photo_file_id']))
        await _edit(query, text, markup.inline_keyboard)
        return ST_TEXT
    prompts = {
        'extra': (
            '🔘 دکمه دلخواه (قرمز) زیر پست\n\n'
            'با این قالب بفرست:\nمتن دکمه | لینک\n\n'
            'مثال:\n🎮 تریلر آیتم جدید | https://youtube.com/...'
        ),
        'addch': (
            '➕ افزودن چنل برای انتشار\n\n'
            'یکی از این‌ها را بفرست:\n'
            '• یک پست از چنل را فوروارد کن (برای چنل خصوصی بهترین راه)\n'
            '• @username چنل یا لینک t.me/…\n'
            '• شناسه عددی -100…\n\n'
            'ربات باید در چنل ادمین با دسترسی «ارسال پست» باشد.'
        ),
    }
    if action == 'set' and arg in SETTING_INPUTS:
        key, title, limit = SETTING_INPUTS[arg]
        values = await asyncio.to_thread(edb.settings, True)
        prompts['set'] = (
            f'{title}\n\nمقدار فعلی: {values[key]}\n\n'
            f'مقدار جدید را بفرست (حداکثر {limit} کاراکتر). برای پیش‌فرض بفرست: پیش‌فرض'
        )
    if action == 'emoji' and arg in EMOJI_INPUTS:
        prompts['emoji'] = (
            f'⭐ ایموجی پریمیوم {EMOJI_INPUTS[arg][1]}\n\n'
            'یک ایموجی پریمیوم بفرست تا جلوی دکمه بنشیند. برای حذف بفرست: حذف'
        )
    if action not in prompts or (action == 'extra' and not arg.isdigit()):
        return ConversationHandler.END
    ctx.user_data[FLOW_KEY] = {'action': action, 'arg': arg}
    await _edit(query, prompts[action] + '\n\n/cancel برای انصراف', [])
    return ST_INPUT


async def receive_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    flow = _flow(ctx)
    if not flow.get('id'):
        return ConversationHandler.END
    message = update.message
    event = await asyncio.to_thread(edb.get_event, flow['id'])
    if not event:
        ctx.user_data.pop(FLOW_KEY, None)
        return ConversationHandler.END
    fields = {'photo_file_id': message.photo[-1].file_id}
    caption = message.caption or ''
    if flow['mode'] == 'new' and caption:
        fields.update(body=caption, body_entities=entities_to_json(message.caption_entities))
    body = fields.get('body', event['body'])
    if utf16_len(body) > CAPTION_MAX:
        await message.reply_text(
            f'❌ توضیحات فعلی {utf16_len(body):,} کاراکتر است؛ با عکس حداکثر {CAPTION_MAX:,} مجاز است.\n'
            'اول توضیحات را کوتاه کن یا عکس دیگری نفرست.'
        )
        return ST_PHOTO
    await asyncio.to_thread(edb.update_event, flow['id'], **fields)
    if flow['mode'] == 'new' and not caption:
        text, markup = _text_prompt(flow, True)
        await message.reply_text(text, reply_markup=markup)
        return ST_TEXT
    note = '✅ عکس ذخیره شد.'
    if event['posts'] and not event['photo_file_id']:
        note += ' ⚠️ پست‌های قبلی متنی بودند؛ برای عکس‌دار شدن، حذف و دوباره منتشر کن.'
    return await _finish(update, ctx, flow['id'], note)


async def photo_step_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    if await _menu_interrupt(update, ctx):
        return ConversationHandler.END
    flow = _flow(ctx)
    if flow.get('mode') == 'new':
        # متن به‌جای عکس = رد کردن عکس و ثبت همین متن به‌عنوان توضیحات
        return await receive_text(update, ctx)
    await update.message.reply_text('🖼 لطفاً عکس بفرست، یا /cancel بزن.')
    return ST_PHOTO


async def skip_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _guard(update):
        return ConversationHandler.END
    await query.answer()
    flow = _flow(ctx)
    if not flow.get('id'):
        return ConversationHandler.END
    text, markup = _text_prompt(flow, False)
    await _edit(query, text, markup.inline_keyboard)
    return ST_TEXT


async def receive_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    if await _menu_interrupt(update, ctx):
        return ConversationHandler.END
    flow = _flow(ctx)
    if not flow.get('id'):
        return ConversationHandler.END
    message = update.message
    text = message.text or ''
    event = await asyncio.to_thread(edb.get_event, flow['id'])
    if not event:
        ctx.user_data.pop(FLOW_KEY, None)
        return ConversationHandler.END
    limit = CAPTION_MAX if event['photo_file_id'] else TEXT_MAX
    if not text.strip():
        await message.reply_text('❌ متن خالی است؛ توضیحات را بفرست.')
        return ST_TEXT
    if utf16_len(text) > limit:
        await message.reply_text(
            f'❌ متن {utf16_len(text):,} کاراکتر است؛ حداکثر {limit:,} مجاز است. کوتاه‌ترش کن و دوباره بفرست.'
        )
        return ST_TEXT
    await asyncio.to_thread(edb.update_event, flow['id'], body=text,
                            body_entities=entities_to_json(message.entities))
    return await _finish(update, ctx, flow['id'], '✅ توضیحات ذخیره شد.')


async def skip_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _guard(update):
        return ConversationHandler.END
    flow = _flow(ctx)
    if not flow.get('id'):
        await query.answer()
        return ConversationHandler.END
    event = await asyncio.to_thread(edb.get_event, flow['id'])
    if not event or not event['photo_file_id']:
        await query.answer('حداقل یکی لازم است: یا عکس بفرست یا توضیحات.', show_alert=True)
        text, markup = _photo_prompt(flow)
        await _edit(query, text, markup.inline_keyboard)
        return ST_PHOTO
    await query.answer()
    ctx.user_data.pop(FLOW_KEY, None)
    try:
        await query.edit_message_text('✅ رویداد فقط با عکس ذخیره شد.')
    except BadRequest:
        pass
    await send_preview(ctx.bot, query.from_user.id, event['id'])
    return ConversationHandler.END


async def text_step_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('📝 متن توضیحات را بفرست، یا /cancel بزن.')
    return ST_TEXT


async def _apply_input(update, ctx, flow):
    message = update.message
    raw = (message.text or '').strip()
    action, arg = flow['action'], flow['arg']
    uid = update.effective_user.id
    if action == 'extra':
        label, sep, url = raw.replace('｜', '|').partition('|')
        label, url = label.strip(), normalize_url(url)
        if not sep or not label or not url:
            raise ValueError('قالب درست: متن دکمه | لینک')
        if len(label) > 64 or '\n' in label:
            raise ValueError('متن دکمه باید یک خط و حداکثر ۶۴ کاراکتر باشد.')
        if not valid_url(url) or len(url) > 500:
            raise ValueError('لینک باید با https:// شروع شود و معتبر باشد.')
        await asyncio.to_thread(edb.update_event, int(arg), extra_btn_text=label, extra_btn_url=url)
        return 'preview', int(arg), '✅ دکمه دلخواه ذخیره شد.'
    if action == 'addch':
        ref = parse_chat_ref(message)
        if ref is None:
            raise ValueError('یک پست از چنل فوروارد کن یا @username / شناسه -100… بفرست.')
        channel, error = await check_chat(ctx.bot, ref)
        if not channel:
            raise ValueError(error)
        note = f'✅ «{channel["title"]}» به لیست انتشار اضافه شد.' + (f'\n⚠️ {error}' if error else '')
        await asyncio.to_thread(log_admin_action, uid, 'event_channel_added', 'channel', channel['chat_id'], '')
        if arg.isdigit():
            return 'picker', int(arg), note
        return 'channels', None, note
    if action == 'set':
        key, title, limit = SETTING_INPUTS[arg]
        if not raw:
            raise ValueError('متن بفرست.')
        if raw in ('پیش‌فرض', 'پیش فرض', 'default'):
            await asyncio.to_thread(edb.put, key, '')
        else:
            value = raw
            if arg == 'surl':
                value = normalize_url(raw)
                if not valid_url(value):
                    raise ValueError('آدرس سایت معتبر نیست؛ مثال: https://atomicshop.ir')
            if len(value) > limit or '\n' in value:
                raise ValueError(f'مقدار باید یک خط و حداکثر {limit} کاراکتر باشد.')
            await asyncio.to_thread(edb.put, key, value)
        await asyncio.to_thread(log_admin_action, uid, 'event_setting', 'setting', key, 'value changed')
        return 'settings', None, f'✅ {title} ذخیره شد.'
    if action == 'emoji':
        key, title = EMOJI_INPUTS[arg]
        if raw == 'حذف':
            await asyncio.to_thread(edb.put, key, '')
            return 'settings', None, f'✅ ایموجی {title} حذف شد.'
        found = appearance.extract_custom_emoji(message)
        if not found:
            raise ValueError('ایموجی پریمیوم پیدا نشد؛ یک ایموجی پریمیوم (متحرک/اختصاصی) بفرست.')
        await asyncio.to_thread(edb.put, key, found['emoji_id'])
        return 'settings', None, f'✅ ایموجی پریمیوم {title} ذخیره شد.'
    raise ValueError('عملیات نامعتبر است.')


async def receive_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return ConversationHandler.END
    if await _menu_interrupt(update, ctx):
        return ConversationHandler.END
    flow = _flow(ctx)
    if not flow.get('action') or update.message is None:
        return ConversationHandler.END
    try:
        target, event_id, note = await _apply_input(update, ctx, flow)
    except ValueError as exc:
        await update.message.reply_text(f'❌ {exc}\nدوباره بفرست یا /cancel بزن.')
        return ST_INPUT
    except Exception as exc:
        _LOG.exception('Event input failed action=%s', flow.get('action'))
        ctx.user_data.pop(FLOW_KEY, None)
        await update.message.reply_text(f'❌ عملیات انجام نشد: {exc}')
        return ConversationHandler.END
    ctx.user_data.pop(FLOW_KEY, None)
    if target == 'preview':
        await send_preview(ctx.bot, update.effective_user.id, event_id, note)
        return ConversationHandler.END
    if target == 'picker':
        event = await asyncio.to_thread(edb.get_event, event_id)
        channels = await asyncio.to_thread(edb.list_channels, True)
        text, rows = channel_picker_view(event, channels)
    elif target == 'channels':
        text, rows = channels_view(await asyncio.to_thread(edb.list_channels, False))
    else:
        text, rows = settings_view(await asyncio.to_thread(edb.settings, True))
    await update.message.reply_text(f'{note}\n\n{text}', reply_markup=InlineKeyboardMarkup(rows),
                                    link_preview_options=_NO_PREVIEW)
    return ConversationHandler.END


async def _drop_empty_draft(flow):
    if flow.get('mode') != 'new' or not flow.get('id'):
        return
    event = await asyncio.to_thread(edb.get_event, flow['id'])
    if event and not event['photo_file_id'] and not event['body']:
        await asyncio.to_thread(edb.delete_event, flow['id'])


async def cancel_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer('لغو شد')
    flow = ctx.user_data.pop(FLOW_KEY, None) or {}
    await _drop_empty_draft(flow)
    if flow.get('id') and flow.get('mode') == 'edit':
        await show_panel(query, flow['id'])
    else:
        await show_home(query)
    return ConversationHandler.END


async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    flow = ctx.user_data.pop(FLOW_KEY, None) or {}
    await _drop_empty_draft(flow)
    await update.message.reply_text(
        'انصراف.', reply_markup=InlineKeyboardMarkup([[_btn('🆕 پنل رویداد', 'gev_home')]]),
    )
    return ConversationHandler.END


def events_conversation_handler():
    cancel_cb = CallbackQueryHandler(cancel_button, pattern=r'^gevs_cancel$')
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(input_start, pattern=INPUT_PATTERN)],
        states={
            ST_PHOTO: [
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, photo_step_text),
                CallbackQueryHandler(skip_photo, pattern=r'^gevs_skipphoto$'),
                cancel_cb,
            ],
            ST_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text),
                MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, text_step_other),
                CallbackQueryHandler(skip_text, pattern=r'^gevs_skiptext$'),
                cancel_cb,
            ],
            ST_INPUT: [
                MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, receive_input),
                cancel_cb,
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_command), cancel_cb],
        allow_reentry=True,
    )


def start_link_handler():
    """باید قبل از CommandHandler('start') عمومی ثبت شود؛ فقط /start evgem_<id>."""
    return CommandHandler('start', event_start, filters=filters.Regex(START_PATTERN))


def register(app):
    """ثبت پنل، گفتگو و ردیاب چنل‌ها؛ فقط از bot.py صدا زده می‌شود."""
    from telegram.ext import ChatMemberHandler

    app.add_handler(events_conversation_handler())
    app.add_handler(CallbackQueryHandler(event_router, pattern=ROUTER_PATTERN))
    # گروه جدا تا هندلر my_chat_member فعلی (جوین اجباری) هم اجرا شود.
    app.add_handler(ChatMemberHandler(track_bot_membership, ChatMemberHandler.MY_CHAT_MEMBER), group=-2)
