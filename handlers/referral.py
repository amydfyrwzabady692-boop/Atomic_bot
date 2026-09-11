"""بخش «دعوت دوستان و جایزه» (رفرال) — سمت کاربر و پنل مدیریت.

- لینک اختصاصی هر کاربر: t.me/<bot>?start=ref_<telegram_id>
- بنر قابل فوروارد با دکمه‌های رنگی: ورود / خرید جم / مسابقه
- مسابقه دوره‌ای با جدول برترین‌ها، ثبت برندگان و پیام به برندگان
- همه متن‌ها، جوایز، عکس بنر و تنظیمات از /admin ← «🎁 دعوت دوستان و مسابقه»

هیچ منطق خرید، پرداخت یا کیف پول در این فایل نیست.
"""
import asyncio
import logging
import re
from urllib.parse import quote

from telegram import (
    CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InlineQueryResultCachedPhoto,
    InputTextMessageContent, LinkPreviewOptions, SwitchInlineQueryChosenChat,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

import appearance
import referral_db as rdb
from admin_notify import is_admin
from db import (
    get_or_create_user, get_support_contact, is_user_blocked,
    list_all_telegram_ids, log_admin_action, upsert_appearance,
)
from keyboards import freefire_products_keyboard, main_menu
from text_safety import markdown_safe

_LOG = logging.getLogger(__name__)

WAIT_REF_INPUT = 71
PAGE_SIZE = 10
START_PAYLOAD_KEY = '_ref_start_payload'
ADMIN_ROUTER_PATTERN = r'^radm_(?!in_)'
ADMIN_INPUT_PATTERN = r'^radm_in_[a-z]+(?:_\d+)?$'

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
_MEDALS = ('🥇', '🥈', '🥉')
_ESCAPED_VALUES = frozenset({'name', 'inviter', 'friend'})
_DISABLED_TEXT = '🎁 بخش دعوت دوستان به‌زودی فعال می‌شود. منتظر خبرهای خوب باش! 🔥'
_BLOCKED_TEXT = '🚫 حساب شما بلاک شده است.'
_BANNER_HINT = (
    "☝️ *بنر اختصاصی تو آماده‌ست!*\n\n"
    "همین پیام بالا رو برای دوستات، گروه‌ها و کانال‌ها *فوروارد* کن.\n"
    "دکمه‌های رنگی همراهش میره و هر کی از طریق اون وارد ربات بشه، به اسم تو ثبت میشه ✅"
)
_DEFAULT_WORDS = ('پیش‌فرض', 'پیش فرض', 'پیشفرض', 'default')
_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')

# action → (کلید تنظیم، عنوان، حداکثر طول، راهنمای متغیرها)
TEXT_INPUTS = {
    'tprize': (
        'referral_prize_text', '🎁 متن جوایز', 1500,
        'مثال:\n🥇 نفر اول: ۱۰۰۰ جم\n🥈 نفر دوم: ۵۰۰ جم\n🥉 نفر سوم: ۲۰۰ جم',
    ),
    'ttitle': ('referral_campaign_title', '🏷 عنوان مسابقه', 200, ''),
    'tpage': (
        'referral_page_text', '📄 متن صفحه دعوت کاربر', 3500,
        'متغیرها: {name} {link} {count} {total} {bought} {rank} {prize} {deadline} {title} {rule}',
    ),
    'tbanner': (
        'referral_banner_text', '🪧 متن بنر اشتراک‌گذاری', 3500,
        'متغیرها: {inviter} {prize} {deadline} {title} {rule}\n'
        'اگر عکس بنر گذاشتی، متن بنر حداکثر ۱۰۲۴ کاراکتر باشد.',
    ),
    'tinvitee': (
        'referral_invitee_text', '🤝 خوش‌آمد کاربر دعوت‌شده', 3500,
        'متغیرها: {name} {inviter} {prize} {deadline} {title} {rule}',
    ),
    'tnotify': (
        'referral_notify_text', '🔔 پیام تبریک به معرف', 3500,
        'متغیرها: {name} {friend} {count} {total} {rank} {rule} {deadline} {prize} {title}',
    ),
    'tannounce': (
        'referral_announce_text', '📣 متن اعلام مسابقه به همه', 3500,
        'متغیرها: {prize} {deadline} {title} {rule}',
    ),
    'bjoin': ('referral_btn_join', '🔘 دکمه ورود (سبز)', 64, 'متن یک‌خطی دکمه'),
    'bgems': ('referral_btn_gems', '🔘 دکمه خرید جم (آبی)', 64, 'متن یک‌خطی دکمه'),
    'bgift': ('referral_btn_gift', '🔘 دکمه مسابقه (قرمز)', 64, 'متن یک‌خطی دکمه'),
}


# ─── Helpers ────────────────────────────────────────────────────────────────────
class _SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def render(template, values=None, markdown=True):
    """قالب مدیر را پر می‌کند؛ نام کاربران برای Markdown امن می‌شود."""
    text = str(template or '')
    if values is None:
        return text
    prepared = {}
    for key, value in values.items():
        raw = '' if value is None else str(value)
        prepared[key] = markdown_safe(raw) if markdown and key in _ESCAPED_VALUES else raw
    try:
        return text.format_map(_SafeDict(prepared))
    except (ValueError, IndexError, AttributeError, KeyError, TypeError):
        for key, value in prepared.items():
            text = text.replace('{' + key + '}', value)
        return text


def _btn(text, callback_data=None, style='primary', **kwargs):
    if callback_data is not None:
        kwargs['callback_data'] = callback_data
    return InlineKeyboardButton(text, style=style, **kwargs)


def medal(rank):
    rank = int(rank or 0)
    return _MEDALS[rank - 1] if 1 <= rank <= 3 else f'{rank}.'


def deadline_text(campaign):
    if not campaign:
        return '⌛ مسابقه‌ی جدید به‌زودی شروع می‌شود؛ از همین حالا دعوت کن!'
    if campaign.get('ends_at') is None:
        return '🔥 مسابقه در جریان است'
    remaining = rdb.format_remaining(campaign['ends_at'], campaign.get('now'))
    if not remaining:
        return '⌛ زمان این دوره تمام شد؛ برندگان به‌زودی اعلام می‌شوند.'
    return f'⏳ فقط {remaining} تا پایان مسابقه ({rdb.format_dt(campaign["ends_at"])})'


def rule_text(mode):
    if mode == 'purchase':
        return '✅ هر دوستی که با لینک تو وارد بشه و اولین خریدش رو انجام بده = ۱ امتیاز'
    return '✅ هر دوستی که با لینک تو وارد ربات بشه = ۱ امتیاز'


def common_values(values, campaign):
    return {
        'prize': values['referral_prize_text'],
        'deadline': deadline_text(campaign),
        'title': (campaign or {}).get('title') or values['referral_campaign_title'],
        'rule': rule_text(values['referral_count_mode']),
    }


def page_values(user, bot_username, values, campaign, stats):
    out = common_values(values, campaign)
    out.update(
        name=user.first_name or 'رفیق',
        link=rdb.referral_link(bot_username, user.id),
        count=f'{int(stats.get("count") or 0):,}',
        total=f'{int(stats.get("total") or 0):,}',
        bought=f'{int(stats.get("bought") or 0):,}',
        rank=str(stats['rank']) if stats.get('rank') else '—',
    )
    return out


async def deliver(bot, chat_id, template, values=None, reply_markup=None, photo=''):
    """ارسال با Markdown؛ اگر متن مدیر Markdown نامعتبر داشت، ساده ارسال می‌شود."""
    attempts = [(True, photo), (False, photo)]
    if photo:
        attempts.append((False, ''))
    last_error = None
    for markdown, pic in attempts:
        text = render(template, values, markdown)
        parse_mode = 'Markdown' if markdown else None
        try:
            if pic and len(text) <= 1024:
                return await bot.send_photo(
                    chat_id=chat_id, photo=pic, caption=text,
                    parse_mode=parse_mode, reply_markup=reply_markup,
                )
            return await bot.send_message(
                chat_id=chat_id, text=text[:4096], parse_mode=parse_mode,
                reply_markup=reply_markup, link_preview_options=_NO_PREVIEW,
            )
        except BadRequest as exc:
            last_error = exc
    raise last_error


async def edit_or_deliver(query, bot, template, values=None, reply_markup=None):
    message = query.message
    if getattr(message, 'text', None) is not None:
        for markdown in (True, False):
            try:
                await query.edit_message_text(
                    render(template, values, markdown)[:4096],
                    parse_mode='Markdown' if markdown else None,
                    reply_markup=reply_markup, link_preview_options=_NO_PREVIEW,
                )
                return
            except BadRequest as exc:
                if 'not modified' in str(exc).lower():
                    return
    await deliver(bot, query.from_user.id, template, values, reply_markup)


def parse_duration_hours(raw):
    text = str(raw or '').translate(_DIGITS).strip().lower()
    match = re.fullmatch(r'(\d{1,5})\s*(h|hour|hours|ساعت|d|day|days|روز)?', text)
    if not match:
        raise ValueError('مدت را مثل 7 (روز) یا 48h (ساعت) بفرست.')
    amount = int(match.group(1))
    unit = match.group(2) or 'd'
    hours = amount if unit in ('h', 'hour', 'hours', 'ساعت') else amount * 24
    if not 1 <= hours <= rdb.MAX_CAMPAIGN_HOURS:
        raise ValueError('مدت باید بین ۱ ساعت تا ۳۶۵ روز باشد.')
    return hours


# ─── Keyboards ──────────────────────────────────────────────────────────────────
def _inline_share_button():
    return _btn(
        '📨 ارسال مستقیم بنر در چت دوستان',
        style='primary',
        switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
            query='دعوت', allow_user_chats=True, allow_group_chats=True,
            allow_channel_chats=True,
        ),
    )


def page_keyboard(bot_username, telegram_id, values, is_admin_viewer=False):
    link = rdb.referral_link(bot_username, telegram_id)
    share_text = '💎 Atomic Shop — خرید جم فری‌فایر با تحویل لحظه‌ای + مسابقه جایزه‌دار 🎁'
    share_url = (
        f'https://t.me/share/url?url={quote(link, safe="")}'
        f'&text={quote(share_text, safe="")}'
    )
    rows = [[_btn('📤 ساخت بنر دعوت با دکمه‌های رنگی', 'refu_banner', 'success')]]
    if rdb.is_on(values, 'referral_inline_share'):
        rows.append([_inline_share_button()])
    rows.append([
        _btn('📋 کپی لینک دعوت', style='primary', copy_text=CopyTextButton(link[:256])),
        _btn('🔗 اشتراک لینک', style='primary', url=share_url),
    ])
    second = []
    if rdb.is_on(values, 'referral_public_top') or is_admin_viewer:
        second.append(_btn('🏆 جدول برترین‌ها', 'refu_top'))
    second.append(_btn('👥 دعوت‌شده‌های من', 'refu_mine'))
    rows.append(second)
    rows.append([_btn('🔙 منوی اصلی', 'home')])
    return InlineKeyboardMarkup(rows)


def banner_keyboard(bot_username, telegram_id, values):
    return InlineKeyboardMarkup([
        [_btn(values['referral_btn_join'], style='success',
              url=rdb.referral_link(bot_username, telegram_id))],
        [_btn(values['referral_btn_gems'], style='primary',
              url=rdb.referral_link(bot_username, telegram_id, 'gem'))],
        [_btn(values['referral_btn_gift'], style='danger',
              url=rdb.referral_link(bot_username, telegram_id, 'gift'))],
    ])


def invitee_keyboard():
    return InlineKeyboardMarkup([
        [_btn('💎 خرید جم فری‌فایر', 'gems', 'success')],
        [_btn('🎁 لینک دعوت من و شرکت در مسابقه', 'refu_home', 'primary')],
    ])


def _page_back_keyboard():
    return InlineKeyboardMarkup([[_btn('🔙 صفحه دعوت من', 'refu_home')]])


# ─── User side ──────────────────────────────────────────────────────────────────
def _collect_page(telegram_id):
    values = rdb.settings()
    campaign = rdb.active_campaign()
    stats = rdb.user_stats(telegram_id, campaign, values['referral_count_mode'])
    return values, campaign, stats


async def _access(user):
    blocked, admin = await asyncio.gather(
        asyncio.to_thread(is_user_blocked, user.id),
        asyncio.to_thread(is_admin, user.id),
    )
    values = await asyncio.to_thread(rdb.settings)
    return bool(blocked), bool(admin), values


async def referral_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """منوی پایین «🎁 دعوت دوستان»، دستور /invite و دکمه refu_home."""
    user = update.effective_user
    if user is None:
        return
    query = update.callback_query
    blocked, admin, values = await _access(user)
    enabled = rdb.is_on(values, 'referral_enabled')
    denial = _BLOCKED_TEXT if blocked and not admin else (
        _DISABLED_TEXT if not enabled and not admin else ''
    )
    if denial:
        if query:
            await query.answer(denial, show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text(denial, reply_markup=main_menu())
        return
    if query:
        await query.answer()
    if not ctx.user_data.get('db_id'):
        db_id, _is_new = await asyncio.to_thread(
            get_or_create_user,
            telegram_id=user.id,
            first_name=user.first_name or '',
            last_name=user.last_name or '',
            username=user.username or '',
            is_premium=bool(user.is_premium),
        )
        ctx.user_data['db_id'] = db_id
    values, campaign, stats = await asyncio.to_thread(_collect_page, user.id)
    template = values['referral_page_text']
    if not enabled:
        template += '\n\n🛠 پیش‌نمایش مدیر — این بخش برای کاربران خاموش است.'
    data = page_values(user, ctx.bot.username, values, campaign, stats)
    markup = page_keyboard(ctx.bot.username, user.id, values, is_admin_viewer=admin)
    if query:
        await edit_or_deliver(query, ctx.bot, template, data, markup)
    else:
        await deliver(ctx.bot, user.id, template, data, markup)


async def send_banner(bot, chat_id, user, values, campaign):
    data = common_values(values, campaign)
    data.update(inviter=user.first_name or 'دوستت', name=user.first_name or 'دوستت')
    await deliver(
        bot, chat_id, values['referral_banner_text'], data,
        banner_keyboard(bot.username, user.id, values),
        photo=values.get('referral_banner_photo') or '',
    )
    rows = []
    if rdb.is_on(values, 'referral_inline_share'):
        rows.append([_inline_share_button()])
    rows.append([_btn('🔙 صفحه دعوت من', 'refu_home')])
    await deliver(bot, chat_id, _BANNER_HINT, None, InlineKeyboardMarkup(rows))


def _public_top_text(telegram_id):
    values = rdb.settings()
    campaign = rdb.active_campaign()
    mode = values['referral_count_mode']
    rows = rdb.leaderboard(campaign, mode, rdb.top_n(values))
    stats = rdb.user_stats(telegram_id, campaign, mode)
    title = (campaign or {}).get('title') or values['referral_campaign_title']
    lines = ['🏆 *جدول برترین دعوت‌کننده‌ها*', title, '━━━━━━━━━━━━━━━']
    if rows:
        for entry in rows:
            mine = ' 👈 تو' if entry['telegram_id'] == str(telegram_id) else ''
            name = markdown_safe(rdb.mask_name(entry['first_name'], entry['username']))
            lines.append(f'{medal(entry["rank"])} {name} — *{entry["count"]:,}* امتیاز{mine}')
    else:
        lines.append('هنوز امتیازی ثبت نشده؛ نفر اول جدول تو باش! 🚀')
    lines.extend([
        '',
        f'📍 رتبه‌ی تو: *{stats["rank"] or "—"}* · امتیاز تو: *{stats["count"]:,}*',
        deadline_text(campaign),
    ])
    return '\n'.join(lines)


def _my_invitees_text(telegram_id):
    user = rdb.find_user(telegram_id)
    if not user:
        return '👥 هنوز کسی را دعوت نکرده‌ای. بنر دعوتت رو بفرست! 🚀'
    items, total = rdb.list_invitees(user['id'], 20, 0)
    bought = sum(1 for item in items if item['bought'])
    lines = [
        '👥 *دعوت‌شده‌های تو*',
        '━━━━━━━━━━━━━━━',
        f'کل: *{total:,}* نفر',
        '',
    ]
    for item in items:
        name = markdown_safe(rdb.mask_name(item['first_name'], item['username']))
        state = '✅ خرید کرده' if item['bought'] else '⏳ هنوز خرید نکرده'
        lines.append(f'• {name} · {rdb.format_date(item["created_at"])} · {state}')
    if total > len(items):
        lines.append(f'… و {total - len(items):,} نفر دیگر')
    if not items:
        lines.append('هنوز کسی با لینک تو وارد نشده. بنر دعوتت رو بفرست! 🚀')
    elif bought:
        lines.extend(['', f'🛒 خریدارهای این لیست: *{bought:,}* نفر'])
    return '\n'.join(lines)


async def referral_user_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    data = query.data or ''
    if data == 'refu_home':
        await referral_menu(update, ctx)
        return
    blocked, admin, values = await _access(user)
    if blocked and not admin:
        await query.answer(_BLOCKED_TEXT, show_alert=True)
        return
    if not rdb.is_on(values, 'referral_enabled') and not admin:
        await query.answer(_DISABLED_TEXT, show_alert=True)
        return
    if data == 'refu_banner':
        await query.answer('بنر اختصاصی تو ساخته شد ✅')
        campaign = await asyncio.to_thread(rdb.active_campaign)
        await send_banner(ctx.bot, user.id, user, values, campaign)
        return
    if data == 'refu_top':
        if not rdb.is_on(values, 'referral_public_top') and not admin:
            await query.answer('جدول برترین‌ها فعلاً مخفی است.', show_alert=True)
            return
        await query.answer()
        text = await asyncio.to_thread(_public_top_text, user.id)
        await edit_or_deliver(query, ctx.bot, text, None, _page_back_keyboard())
        return
    if data == 'refu_mine':
        await query.answer()
        text = await asyncio.to_thread(_my_invitees_text, user.id)
        await edit_or_deliver(query, ctx.bot, text, None, _page_back_keyboard())
        return
    await query.answer()


async def referral_inline_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ارسال مستقیم بنر با @bot — فقط اگر مدیر روشن کرده و /setinline فعال باشد."""
    inline = update.inline_query
    if inline is None:
        return
    user = inline.from_user
    try:
        values = await asyncio.to_thread(rdb.settings)
        allowed = (
            rdb.is_on(values, 'referral_enabled')
            and rdb.is_on(values, 'referral_inline_share')
            and not await asyncio.to_thread(is_user_blocked, user.id)
        )
        if not allowed:
            await inline.answer([], cache_time=10, is_personal=True)
            return
        campaign = await asyncio.to_thread(rdb.active_campaign)
        data = common_values(values, campaign)
        data.update(inviter=user.first_name or 'دوستت', name=user.first_name or 'دوستت')
        markup = banner_keyboard(ctx.bot.username, user.id, values)
        photo = values.get('referral_banner_photo') or ''
        for markdown in (True, False):
            text = render(values['referral_banner_text'], data, markdown)
            parse_mode = 'Markdown' if markdown else None
            if photo and len(text) <= 1024:
                result = InlineQueryResultCachedPhoto(
                    id='ref_banner', photo_file_id=photo, title='🎁 بنر دعوت اختصاصی من',
                    caption=text, parse_mode=parse_mode, reply_markup=markup,
                )
            else:
                result = InlineQueryResultArticle(
                    id='ref_banner',
                    title='🎁 ارسال بنر دعوت اختصاصی من',
                    description='بنر با دکمه‌های رنگی ورود، خرید جم و مسابقه جایزه‌دار',
                    input_message_content=InputTextMessageContent(
                        text[:4096], parse_mode=parse_mode,
                        link_preview_options=_NO_PREVIEW,
                    ),
                    reply_markup=markup,
                )
            try:
                await inline.answer([result], cache_time=0, is_personal=True)
                return
            except BadRequest:
                continue
    except Exception:
        _LOG.exception('Referral inline query failed')


# ─── /start deep link ───────────────────────────────────────────────────────────
def remember_start_payload(update, ctx):
    """جوین اجباری: لینک دعوت قبل از عضویت در کانال گم نشود."""
    message = getattr(update, 'message', None)
    text = getattr(message, 'text', None)
    if not isinstance(text, str) or not text.startswith('/start'):
        return
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return
    payload = parts[1].strip()
    referrer, _kind = rdb.parse_payload(payload)
    if referrer is not None:
        ctx.user_data[START_PAYLOAD_KEY] = payload


def pop_start_payload(ctx):
    return ctx.user_data.pop(START_PAYLOAD_KEY, None)


async def resume_start_payload(update, ctx, db_id, is_new):
    payload = pop_start_payload(ctx)
    if payload:
        await handle_start_payload(update, ctx, db_id, is_new, payload)


async def _send_gem_menu(bot, chat_id):
    payload = appearance.message_kwargs('t.ff.hdr', appearance.DEFAULTS['t.ff.hdr'])
    try:
        await bot.send_message(chat_id=chat_id, **payload,
                               reply_markup=freefire_products_keyboard())
    except BadRequest:
        await bot.send_message(chat_id=chat_id, text=payload['text'],
                               reply_markup=freefire_products_keyboard())


async def _notify_referrer(bot, recorded, invitee, values, campaign):
    if not rdb.is_on(values, 'referral_notify_referrer'):
        return
    try:
        stats = await asyncio.to_thread(
            rdb.user_stats, recorded['referrer_telegram_id'], campaign,
            values['referral_count_mode'],
        )
        data = common_values(values, campaign)
        data.update(
            name=recorded.get('referrer_first_name') or 'رفیق',
            friend=rdb.mask_name(invitee.first_name, invitee.username),
            count=f'{stats["count"]:,}',
            total=f'{stats["total"]:,}',
            rank=str(stats['rank']) if stats.get('rank') else '—',
        )
        await deliver(
            bot, int(recorded['referrer_telegram_id']), values['referral_notify_text'],
            data, InlineKeyboardMarkup([[_btn('🎁 صفحه دعوت من', 'refu_home', 'success')]]),
        )
    except Exception:
        _LOG.info('Referrer notification skipped', exc_info=True)


async def handle_start_payload(update, ctx, db_id, is_new, payload):
    """بعد از خوش‌آمد /start: ثبت دعوت (فقط کاربر جدید) و هدایت دکمه‌های بنر."""
    ctx.user_data.pop(START_PAYLOAD_KEY, None)
    referrer_tg, kind = rdb.parse_payload(payload)
    user = update.effective_user
    if referrer_tg is None or user is None:
        return
    values = await asyncio.to_thread(rdb.settings)
    enabled = rdb.is_on(values, 'referral_enabled')
    recorded = None
    if enabled and is_new and db_id and int(referrer_tg) != int(user.id):
        try:
            recorded = await asyncio.to_thread(rdb.record_referral, user.id, db_id, referrer_tg)
        except Exception:
            _LOG.exception('Referral could not be recorded')
    campaign = None
    if enabled and (recorded or kind == 'gift'):
        campaign = await asyncio.to_thread(rdb.active_campaign)
    if recorded:
        await _notify_referrer(ctx.bot, recorded, user, values, campaign)
        data = common_values(values, campaign)
        data.update(
            name=user.first_name or 'رفیق',
            inviter=recorded.get('referrer_first_name') or 'یک دوست',
        )
        await deliver(ctx.bot, user.id, values['referral_invitee_text'], data, invitee_keyboard())
    if kind == 'gem':
        await _send_gem_menu(ctx.bot, user.id)
    elif kind == 'gift' and enabled and not recorded:
        stats = await asyncio.to_thread(
            rdb.user_stats, user.id, campaign, values['referral_count_mode'],
        )
        await deliver(
            ctx.bot, user.id, values['referral_page_text'],
            page_values(user, ctx.bot.username, values, campaign, stats),
            page_keyboard(ctx.bot.username, user.id, values),
        )


# ─── Admin panel ────────────────────────────────────────────────────────────────
async def _admin_guard(update):
    uid = update.effective_user.id if update.effective_user else None
    if await asyncio.to_thread(is_admin, uid):
        return True
    if update.callback_query:
        await update.callback_query.answer('دسترسی ندارید.', show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text('دسترسی ندارید.')
    return False


async def _admin_edit(query, bot, text, rows, markdown=True):
    markup = InlineKeyboardMarkup(rows) if rows else None
    for md in ((True, False) if markdown else (False,)):
        try:
            await query.edit_message_text(
                text[:4096], parse_mode='Markdown' if md else None,
                reply_markup=markup, link_preview_options=_NO_PREVIEW,
            )
            return
        except BadRequest as exc:
            if 'not modified' in str(exc).lower():
                return
    await bot.send_message(
        chat_id=query.from_user.id, text=text[:4096], reply_markup=markup,
        link_preview_options=_NO_PREVIEW,
    )


def _home_button():
    return [_btn('🎁 پنل دعوت دوستان', 'radm_home')]


def _entry_line(entry):
    name = markdown_safe(rdb.display_name(entry['first_name'], entry['last_name'], entry['username']), 60)
    return f'{medal(entry["rank"])} {name} · `{entry["telegram_id"] or "—"}` — *{entry["count"]:,}*'


def _winner_line(winner):
    name = markdown_safe(str(winner.get('name') or '—'), 60)
    return (
        f'{medal(winner.get("rank"))} {name} · `{winner.get("telegram_id") or "—"}` '
        f'— *{int(winner.get("count") or 0):,}*'
    )


def admin_home_rows(values):
    enabled = rdb.is_on(values, 'referral_enabled')
    return [
        [_btn('🔴 خاموش کردن بخش دعوت' if enabled else '🟢 روشن کردن بخش دعوت',
              'radm_tg_enabled', 'danger' if enabled else 'success')],
        [_btn('🏆 جدول مسابقه فعلی', 'radm_top_0'), _btn('👥 همه معرف‌ها', 'radm_all_0')],
        [_btn('🏁 شروع مسابقه جدید', 'radm_in_newcamp', 'success'),
         _btn('⏱ تغییر زمان پایان', 'radm_in_extend')],
        [_btn('⏹ پایان مسابقه و ثبت برندگان', 'radm_close', 'danger')],
        [_btn('📜 برندگان دوره‌های قبل', 'radm_hist')],
        [_btn('✏️ متن‌ها، جوایز و بنر', 'radm_texts'), _btn('⚙️ تنظیمات', 'radm_settings')],
        [_btn('📣 اعلام مسابقه به همه کاربران', 'radm_announce', 'success')],
        [_btn('📨 پیام به برترین‌ها', 'radm_in_msgtop'), _btn('📨 پیام به همه معرف‌ها', 'radm_in_msgall')],
        [_btn('👀 پیش‌نمایش صفحه و بنر', 'radm_preview')],
        [_btn('🔄 بروزرسانی', 'radm_home'), _btn('🏠 منوی اصلی', 'adm_home')],
    ]


def _admin_home_data():
    values = rdb.settings(force=True)
    campaign = rdb.active_campaign()
    mode = values['referral_count_mode']
    return values, campaign, rdb.overview(campaign, mode), rdb.leaderboard(campaign, mode, 5)


def admin_home_text(values, campaign, stats, top):
    enabled = rdb.is_on(values, 'referral_enabled')
    purchase = values['referral_count_mode'] == 'purchase'
    lines = [
        '🎁 *مدیریت دعوت دوستان و مسابقه*',
        '━━━━━━━━━━━━━━━',
        f'وضعیت بخش: {"🟢 روشن" if enabled else "🔴 خاموش (کاربران نمی‌بینند)"}',
        f'ملاک امتیاز: {"🛒 دعوت‌شده‌ای که خرید کرده" if purchase else "👤 هر عضو جدید با لینک"}',
        '',
    ]
    if campaign:
        title = campaign['title'] or values['referral_campaign_title']
        lines.append(f'🏆 مسابقه فعلی: {markdown_safe(title, 120)} (#{campaign["id"]})')
        lines.append(f'▶️ شروع: {rdb.format_dt(campaign["starts_at"])}')
        if campaign['ends_at'] is not None:
            lines.append(f'⏹ پایان: {rdb.format_dt(campaign["ends_at"])}')
        if campaign['ended']:
            lines.append('⚠️ *زمان مسابقه تمام شده* — «پایان مسابقه و ثبت برندگان» را بزن.')
        elif campaign['ends_at'] is not None:
            lines.append(
                f'⏳ باقی‌مانده: {rdb.format_remaining(campaign["ends_at"], campaign["now"])}'
            )
    else:
        lines.append('⚠️ مسابقه‌ی فعالی نیست — «🏁 شروع مسابقه جدید» را بزن.')
    if values['referral_prize_text'] == rdb.DEFAULT_SETTINGS['referral_prize_text']:
        lines.append('⚠️ متن جوایز هنوز تنظیم نشده (✏️ متن‌ها ← 🎁 متن جوایز).')
    if enabled:
        lines.append('ℹ️ دکمه منو با اولین نمایش منوی اصلی (مثلاً /start) برای کاربر ظاهر می‌شود.')
    lines.extend([
        '',
        '📊 *آمار*',
        f'• کل دعوت‌ها: *{stats["total"]:,}* · ۲۴ ساعت اخیر: *{stats["last_day"]:,}*',
        f'• کل معرف‌ها: *{stats["referrers"]:,}*',
        f'• امتیازهای این دوره: *{stats["period_points"]:,}* از *{stats["period_referrers"]:,}* نفر',
        '',
        '🥇 *برترین‌های فعلی*',
    ])
    lines.extend([_entry_line(entry) for entry in top] or ['هنوز امتیازی ثبت نشده.'])
    return '\n'.join(lines)


async def show_admin_home(query, ctx, notice=''):
    values, campaign, stats, top = await asyncio.to_thread(_admin_home_data)
    text = admin_home_text(values, campaign, stats, top)
    if notice:
        text = f'{notice}\n\n{text}'
    await _admin_edit(query, ctx.bot, text, admin_home_rows(values))


def admin_settings_rows(values):
    purchase = values['referral_count_mode'] == 'purchase'

    def state(key, on_text, off_text):
        return on_text if rdb.is_on(values, key) else off_text

    return [
        [_btn(f'🎯 ملاک امتیاز: {"🛒 اولین خرید" if purchase else "👤 عضویت"} (تغییر)', 'radm_tg_mode')],
        [_btn(f'🔔 پیام تبریک به معرف: {state("referral_notify_referrer", "روشن ✅", "خاموش")}',
              'radm_tg_notify')],
        [_btn(f'👁 جدول برای کاربران: {state("referral_public_top", "نمایش ✅", "مخفی")}',
              'radm_tg_public')],
        [_btn(f'📨 ارسال مستقیم بنر (Inline): {state("referral_inline_share", "روشن ✅", "خاموش")}',
              'radm_tg_inline')],
        [_btn(f'🔢 تعداد نفرات جدول و برندگان: {rdb.top_n(values)}', 'radm_in_topn')],
        [_btn('🔙 پنل دعوت', 'radm_home')],
    ]


_SETTINGS_TEXT = (
    '⚙️ *تنظیمات دعوت دوستان*\n'
    '━━━━━━━━━━━━━━━\n'
    '🎯 *ملاک امتیاز*\n'
    '• 👤 عضویت: هر عضو جدید با لینک = ۱ امتیاز (رشد سریع‌تر)\n'
    '• 🛒 اولین خرید: فقط دعوت‌شده‌ای که خرید موفق داشته حساب می‌شود (ضد اکانت فیک)\n\n'
    '📨 *ارسال مستقیم (Inline)*: کاربر بنر را مستقیم داخل چت دوستانش می‌فرستد.\n'
    '⚠️ برای کار کردن، در @BotFather دستور /setinline را برای ربات فعال کن.\n\n'
    '🚫 کاربران بلاک‌شده (معرف یا دعوت‌شده) در امتیازها حساب نمی‌شوند.'
)


def admin_texts_rows():
    return [
        [_btn('🎁 متن جوایز', 'radm_in_tprize', 'success')],
        [_btn('🏷 عنوان مسابقه', 'radm_in_ttitle'), _btn('📄 متن صفحه دعوت', 'radm_in_tpage')],
        [_btn('🪧 متن بنر', 'radm_in_tbanner'), _btn('🖼 عکس بنر', 'radm_in_photo')],
        [_btn('🤝 خوش‌آمد دعوت‌شده', 'radm_in_tinvitee'), _btn('🔔 پیام تبریک معرف', 'radm_in_tnotify')],
        [_btn('📣 متن اعلام مسابقه', 'radm_in_tannounce')],
        [_btn('🔘 دکمه ورود', 'radm_in_bjoin'), _btn('🔘 دکمه خرید جم', 'radm_in_bgems')],
        [_btn('🔘 دکمه مسابقه', 'radm_in_bgift'), _btn('📱 دکمه منوی اصلی', 'radm_in_menulabel')],
        [_btn('↩️ بازگشت همه متن‌ها به پیش‌فرض', 'radm_resettexts', 'danger')],
        [_btn('🔙 پنل دعوت', 'radm_home')],
    ]


def admin_texts_text(values):
    photo = 'تنظیم شده ✅' if values.get('referral_banner_photo') else 'ندارد'
    return (
        '✏️ *متن‌ها، جوایز و بنر*\n'
        '━━━━━━━━━━━━━━━\n'
        'هر مورد را بزن؛ مقدار فعلی را می‌بینی و متن جدید را می‌فرستی.\n\n'
        '• بولد: \\*متن\\* · قابل کپی: \\`متن\\`\n'
        '• برای برگرداندن هر مورد به پیش‌فرض، کلمه «پیش‌فرض» را بفرست.\n'
        '• بعد از ذخیره، پیش‌نمایش همان متن برایت ارسال می‌شود.\n\n'
        f'🖼 عکس بنر: {photo}'
    )


def _admin_board_data(page, window):
    values = rdb.settings()
    campaign = rdb.active_campaign() if window else None
    mode = values['referral_count_mode'] if window else 'join'
    rows = rdb.leaderboard(campaign, mode, PAGE_SIZE, page * PAGE_SIZE, window=window)
    referrers, points = rdb.count_leaderboard(campaign, mode, window=window)
    return values, campaign, rows, referrers, points


async def _show_admin_board(query, ctx, page, window):
    page = max(0, int(page))
    values, campaign, rows, referrers, points = await asyncio.to_thread(
        _admin_board_data, page, window,
    )
    pages = max(1, (referrers + PAGE_SIZE - 1) // PAGE_SIZE)
    if window:
        title = (campaign or {}).get('title') or values['referral_campaign_title']
        lines = ['🏆 *جدول مسابقه فعلی*', markdown_safe(title, 120)]
        if not campaign:
            lines.append('⚠️ مسابقه‌ی فعالی نیست؛ امتیازها از ابتدا شمرده شده‌اند.')
    else:
        lines = ['👥 *همه معرف‌ها (از ابتدا)*']
    lines.extend([
        f'معرف‌ها: *{referrers:,}* · امتیازها: *{points:,}* · صفحه {page + 1} از {pages}',
        '━━━━━━━━━━━━━━━',
    ])
    lines.extend([_entry_line(entry) for entry in rows] or ['موردی ثبت نشده.'])
    buttons = []
    for entry in rows:
        if entry['telegram_id'].isdigit():
            label = rdb.display_name(entry['first_name'], entry['last_name'], entry['username'])
            buttons.append([_btn(
                f'{entry["rank"]}. {label[:28]} — {entry["count"]:,}',
                f'radm_u_{entry["telegram_id"]}',
            )])
    prefix = 'radm_top_' if window else 'radm_all_'
    nav = []
    if page > 0:
        nav.append(_btn('◀️ قبلی', f'{prefix}{page - 1}'))
    if page + 1 < pages:
        nav.append(_btn('بعدی ▶️', f'{prefix}{page + 1}'))
    if nav:
        buttons.append(nav)
    if window:
        buttons.append([_btn('📨 پیام به برترین‌ها', 'radm_in_msgtop')])
    buttons.append(_home_button())
    await _admin_edit(query, ctx.bot, '\n'.join(lines), buttons)


def _admin_user_data(telegram_id):
    values = rdb.settings()
    campaign = rdb.active_campaign()
    user = rdb.find_user(telegram_id)
    if not user:
        return values, campaign, None, None, [], 0
    stats = rdb.user_stats(telegram_id, campaign, values['referral_count_mode'])
    invitees, total = rdb.list_invitees(user['id'], 8, 0)
    return values, campaign, user, stats, invitees, total


def _invitee_line(item):
    name = markdown_safe(rdb.display_name(item['first_name'], item['last_name'], item['username']), 50)
    state = '✅ خرید' if item['bought'] else '⏳'
    blocked = ' · 🚫' if item['blocked'] else ''
    return f'• {name} · `{item["telegram_id"]}` · {rdb.format_date(item["created_at"])} · {state}{blocked}'


async def _show_admin_user(query, ctx, telegram_id):
    _values, _campaign, user, stats, invitees, total = await asyncio.to_thread(
        _admin_user_data, telegram_id,
    )
    if not user:
        await _admin_edit(query, ctx.bot, 'کاربر پیدا نشد.', [_home_button()], markdown=False)
        return
    name = markdown_safe(rdb.display_name(user['first_name'], user['last_name'], user['username']), 80)
    lines = [
        '👤 *کارت دعوت کاربر*',
        '━━━━━━━━━━━━━━━',
        f'نام: {name}',
        f'شناسه عددی: `{user["telegram_id"]}`',
        f'وضعیت: {"🚫 بلاک" if user["blocked"] else "✅ فعال"}',
        f'عضویت: {rdb.format_date(user["joined_at"])}',
        '',
        f'🏅 رتبه در مسابقه فعلی: *{stats["rank"] or "—"}*',
        f'🎯 امتیاز دوره: *{stats["count"]:,}*',
        f'👥 کل دعوت‌ها: *{stats["total"]:,}* · 🛒 خریدار: *{stats["bought"]:,}*',
        f'🕒 آخرین دعوت: {rdb.format_dt(stats["last_at"])}',
    ]
    if invitees:
        lines.extend(['', '*آخرین دعوت‌شده‌ها:*'])
        lines.extend(_invitee_line(item) for item in invitees)
    tg = user['telegram_id']
    rows = [
        [_btn('💬 ارسال پیام / تیکت به کاربر', f'radm_in_msg_{tg}', 'success')],
        [_btn(f'👥 همه دعوت‌شده‌ها ({total:,})', f'radm_inv_{tg}_0')],
        [_btn('👤 کارت کامل کاربر · کیف پول و بلاک', f'adm_user_{tg}')],
        [_btn('🏆 جدول مسابقه', 'radm_top_0'), _btn('🎁 پنل دعوت', 'radm_home')],
    ]
    await _admin_edit(query, ctx.bot, '\n'.join(lines), rows)


async def _show_admin_invitees(query, ctx, telegram_id, page):
    page = max(0, int(page))
    user = await asyncio.to_thread(rdb.find_user, telegram_id)
    if not user:
        await _admin_edit(query, ctx.bot, 'کاربر پیدا نشد.', [_home_button()], markdown=False)
        return
    per_page = 15
    items, total = await asyncio.to_thread(rdb.list_invitees, user['id'], per_page, page * per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    name = markdown_safe(rdb.display_name(user['first_name'], user['last_name'], user['username']), 60)
    lines = [
        f'👥 *دعوت‌شده‌های {name}*',
        f'کل: *{total:,}* · صفحه {page + 1} از {pages}',
        '━━━━━━━━━━━━━━━',
    ]
    lines.extend([_invitee_line(item) for item in items] or ['موردی ثبت نشده.'])
    nav = []
    if page > 0:
        nav.append(_btn('◀️ قبلی', f'radm_inv_{telegram_id}_{page - 1}'))
    if page + 1 < pages:
        nav.append(_btn('بعدی ▶️', f'radm_inv_{telegram_id}_{page + 1}'))
    rows = [nav] if nav else []
    rows.append([_btn('🔙 کارت کاربر', f'radm_u_{telegram_id}')])
    await _admin_edit(query, ctx.bot, '\n'.join(lines), rows)


def _campaign_summary(campaign):
    title = campaign['title'] or 'مسابقه'
    return (
        f'#{campaign["id"]} · {title} · {rdb.format_date(campaign["starts_at"])}'
        f' تا {rdb.format_date(campaign["ends_at"])}'
    )


async def _show_campaign(query, ctx, campaign_id):
    campaign = await asyncio.to_thread(rdb.get_campaign, campaign_id)
    if not campaign:
        await _admin_edit(query, ctx.bot, 'دوره پیدا نشد.', [[_btn('🔙 برندگان قبلی', 'radm_hist')]], markdown=False)
        return
    lines = [
        f'📜 *{markdown_safe(_campaign_summary(campaign), 200)}*',
        f'ملاک: {"🛒 اولین خرید" if campaign["count_mode"] == "purchase" else "👤 عضویت"}',
        '',
        '🎁 *جوایز اعلام‌شده:*',
        markdown_safe(campaign['prize_text'] or '—', 1200),
        '',
        '🏆 *برندگان:*',
    ]
    lines.extend([_winner_line(w) for w in campaign['winners']] or ['امتیازی ثبت نشده بود.'])
    rows = [
        [_btn(f'{w.get("rank")}. {str(w.get("name") or "")[:30]}', f'radm_u_{w["telegram_id"]}')]
        for w in campaign['winners'] if str(w.get('telegram_id') or '').isdigit()
    ]
    if campaign['winners']:
        rows.append([_btn('📨 پیام به برندگان این دوره', f'radm_in_msgwin_{campaign["id"]}', 'success')])
    rows.append([_btn('🔙 برندگان قبلی', 'radm_hist')])
    await _admin_edit(query, ctx.bot, '\n'.join(lines), rows)


async def _run_announce(query, ctx):
    if ctx.bot_data.get('_ref_announce_running'):
        await query.answer('ارسال قبلی هنوز در جریان است.', show_alert=True)
        return
    ctx.bot_data['_ref_announce_running'] = True
    try:
        await query.answer('ارسال شروع شد…')
        ids = await asyncio.to_thread(list_all_telegram_ids)
        values = await asyncio.to_thread(rdb.settings, True)
        campaign = await asyncio.to_thread(rdb.active_campaign)
        data = common_values(values, campaign)
        markup = InlineKeyboardMarkup([[
            _btn('🎁 دریافت لینک دعوت و شرکت در مسابقه', 'refu_home', 'success'),
        ]])
        status = await ctx.bot.send_message(
            chat_id=query.from_user.id, text=f'⏳ ارسال اعلام مسابقه به {len(ids):,} کاربر شروع شد…',
        )
        sent = failed = 0
        for index, tg in enumerate(ids, start=1):
            try:
                await deliver(
                    ctx.bot, int(tg), values['referral_announce_text'], data, markup,
                    photo=values.get('referral_banner_photo') or '',
                )
                sent += 1
            except Exception:
                failed += 1
            if index % 25 == 0:
                await asyncio.sleep(1)
        await asyncio.to_thread(
            log_admin_action, query.from_user.id, 'referral_announce', 'referral', '',
            f'sent={sent} failed={failed}',
        )
        await status.edit_text(
            f'✅ اعلام مسابقه ارسال شد.\nموفق: {sent:,}\nناموفق: {failed:,}',
            reply_markup=InlineKeyboardMarkup([_home_button()]),
        )
    finally:
        ctx.bot_data.pop('_ref_announce_running', None)


async def _send_preview(query, ctx):
    user = query.from_user
    values, campaign, stats = await asyncio.to_thread(_collect_page, user.id)
    bot = ctx.bot
    await deliver(bot, user.id, '👀 *پیش‌نمایش ۱ — صفحه دعوت کاربر:*')
    await deliver(
        bot, user.id, values['referral_page_text'],
        page_values(user, bot.username, values, campaign, stats),
        page_keyboard(bot.username, user.id, values, is_admin_viewer=True),
    )
    await deliver(bot, user.id, '👀 *پیش‌نمایش ۲ — بنر قابل فوروارد:*')
    await send_banner(bot, user.id, user, values, campaign)
    sample = common_values(values, campaign)
    sample.update(name='علی', inviter=user.first_name or 'رضا', friend='سا•••',
                  count='12', total='30', rank='2')
    await deliver(bot, user.id, '👀 *پیش‌نمایش ۳ — خوش‌آمد کاربر دعوت‌شده:*')
    await deliver(bot, user.id, values['referral_invitee_text'], sample, invitee_keyboard())
    await deliver(bot, user.id, '👀 *پیش‌نمایش ۴ — پیام تبریک به معرف:*')
    await deliver(bot, user.id, values['referral_notify_text'], sample)


async def referral_admin_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None or not await _admin_guard(update):
        return
    data = query.data or ''
    self_answer = data in ('radm_announceok', 'radm_preview', 'radm_closeok')
    if not self_answer:
        await query.answer()
    uid = query.from_user.id

    if data == 'radm_home':
        await show_admin_home(query, ctx)
    elif data.startswith('radm_tg_'):
        name = data[len('radm_tg_'):]
        keys = {
            'enabled': 'referral_enabled', 'notify': 'referral_notify_referrer',
            'public': 'referral_public_top', 'inline': 'referral_inline_share',
        }
        values = await asyncio.to_thread(rdb.settings, True)
        if name == 'mode':
            key = 'referral_count_mode'
            new = 'join' if values[key] == 'purchase' else 'purchase'
        elif name in keys:
            key = keys[name]
            new = '0' if rdb.is_on(values, key) else '1'
        else:
            return
        await asyncio.to_thread(rdb.put, key, new)
        await asyncio.to_thread(log_admin_action, uid, 'referral_setting', 'setting', key, f'value={new}')
        if name == 'enabled':
            await show_admin_home(query, ctx)
        else:
            values = await asyncio.to_thread(rdb.settings, True)
            await _admin_edit(query, ctx.bot, _SETTINGS_TEXT, admin_settings_rows(values))
    elif data == 'radm_settings':
        values = await asyncio.to_thread(rdb.settings, True)
        await _admin_edit(query, ctx.bot, _SETTINGS_TEXT, admin_settings_rows(values))
    elif data == 'radm_texts':
        values = await asyncio.to_thread(rdb.settings, True)
        await _admin_edit(query, ctx.bot, admin_texts_text(values), admin_texts_rows())
    elif data == 'radm_resettexts':
        await _admin_edit(query, ctx.bot, (
            '↩️ همه متن‌ها، جوایز، عنوان و متن دکمه‌های بنر به پیش‌فرض برمی‌گردند.\n'
            'تنظیمات روشن/خاموش، عکس بنر و مسابقه‌ها دست نمی‌خورند. مطمئنی؟'
        ), [
            [_btn('✅ بله، برگردان', 'radm_resettextsok', 'danger')],
            [_btn('🔙 انصراف', 'radm_texts')],
        ], markdown=False)
    elif data == 'radm_resettextsok':
        for key in rdb.TEXT_KEYS:
            await asyncio.to_thread(rdb.put, key, '')
        await asyncio.to_thread(log_admin_action, uid, 'referral_texts_reset', 'referral', '', '')
        values = await asyncio.to_thread(rdb.settings, True)
        await _admin_edit(query, ctx.bot, '✅ متن‌ها به پیش‌فرض برگشتند.\n\n' + admin_texts_text(values),
                          admin_texts_rows())
    elif data.startswith('radm_top_') or data.startswith('radm_all_'):
        page = data.rsplit('_', 1)[1]
        if page.isdigit():
            await _show_admin_board(query, ctx, int(page), data.startswith('radm_top_'))
    elif re.fullmatch(r'radm_u_\d+', data):
        await _show_admin_user(query, ctx, data.rsplit('_', 1)[1])
    elif re.fullmatch(r'radm_inv_\d+_\d+', data):
        _prefix, _inv, tg, page = data.split('_')
        await _show_admin_invitees(query, ctx, tg, int(page))
    elif data == 'radm_close':
        values = await asyncio.to_thread(rdb.settings, True)
        campaign = await asyncio.to_thread(rdb.active_campaign)
        if not campaign:
            await _admin_edit(query, ctx.bot, 'مسابقه‌ی بازی وجود ندارد.', [
                [_btn('🏁 شروع مسابقه جدید', 'radm_in_newcamp', 'success')], _home_button(),
            ], markdown=False)
            return
        top = await asyncio.to_thread(
            rdb.leaderboard, campaign, values['referral_count_mode'], rdb.top_n(values),
        )
        lines = [
            '⏹ *پایان مسابقه و ثبت برندگان*',
            f'این {rdb.top_n(values)} نفر به‌عنوان برندگان دوره #{campaign["id"]} ذخیره می‌شوند:',
            '━━━━━━━━━━━━━━━',
        ]
        lines.extend([_entry_line(entry) for entry in top] or ['هنوز امتیازی ثبت نشده.'])
        lines.extend(['', 'بعد از بستن، امتیازهای دوره‌ی بعد از صفر شروع می‌شود. مطمئنی؟'])
        await _admin_edit(query, ctx.bot, '\n'.join(lines), [
            [_btn('✅ بله، پایان و ثبت برندگان', 'radm_closeok', 'danger')],
            [_btn('🔙 انصراف', 'radm_home')],
        ])
    elif data == 'radm_closeok':
        await query.answer('در حال ثبت برندگان…')
        values = await asyncio.to_thread(rdb.settings, True)
        closed = await asyncio.to_thread(rdb.close_campaign, rdb.top_n(values))
        if not closed:
            await show_admin_home(query, ctx, notice='مسابقه‌ی بازی وجود نداشت.')
            return
        await asyncio.to_thread(
            log_admin_action, uid, 'referral_campaign_closed', 'campaign', closed['id'],
            f'winners={len(closed["winners"])}',
        )
        lines = [f'✅ *دوره #{closed["id"]} بسته شد و برندگان ثبت شدند*', '━━━━━━━━━━━━━━━']
        lines.extend([_winner_line(w) for w in closed['winners']] or ['امتیازی ثبت نشده بود.'])
        lines.extend(['', 'برای جایزه، از «کارت کامل کاربر» کیف پول برنده را شارژ کن یا به او پیام بده.'])
        rows = [
            [_btn(f'{w["rank"]}. {str(w["name"])[:30]}', f'radm_u_{w["telegram_id"]}')]
            for w in closed['winners'] if str(w.get('telegram_id') or '').isdigit()
        ]
        if closed['winners']:
            rows.append([_btn('📨 پیام به برندگان', f'radm_in_msgwin_{closed["id"]}', 'success')])
        rows.append([_btn('🏁 شروع مسابقه جدید', 'radm_in_newcamp', 'success')])
        rows.append(_home_button())
        await _admin_edit(query, ctx.bot, '\n'.join(lines), rows)
    elif data == 'radm_hist':
        campaigns = await asyncio.to_thread(rdb.list_campaigns, 10)
        lines = ['📜 *برندگان دوره‌های قبل*', '━━━━━━━━━━━━━━━']
        rows = []
        for campaign in campaigns:
            first = campaign['winners'][0] if campaign['winners'] else None
            best = f' · 🥇 {first["name"]} ({first["count"]})' if first else ''
            lines.append(markdown_safe(_campaign_summary(campaign) + best, 300))
            rows.append([_btn(f'#{campaign["id"]} · {campaign["title"][:30] or "مسابقه"}',
                              f'radm_camp_{campaign["id"]}')])
        if not campaigns:
            lines.append('هنوز دوره‌ای بسته نشده است.')
        rows.append(_home_button())
        await _admin_edit(query, ctx.bot, '\n'.join(lines), rows)
    elif re.fullmatch(r'radm_camp_\d+', data):
        await _show_campaign(query, ctx, int(data.rsplit('_', 1)[1]))
    elif data == 'radm_announce':
        ids = await asyncio.to_thread(list_all_telegram_ids)
        await _admin_edit(query, ctx.bot, (
            f'📣 اعلام مسابقه برای {len(ids):,} کاربر ربات ارسال می‌شود.\n'
            'متن از «✏️ متن‌ها ← 📣 متن اعلام مسابقه» و عکس از «🖼 عکس بنر» خوانده می‌شود.\n'
            'زیر پیام دکمه‌ی «دریافت لینک دعوت» قرار می‌گیرد.\n\n'
            'پیشنهاد: اول «👀 پیش‌نمایش» و متن جوایز را چک کن. ارسال شود؟'
        ), [
            [_btn('✅ بله، ارسال به همه', 'radm_announceok', 'success')],
            [_btn('🔙 انصراف', 'radm_home')],
        ], markdown=False)
    elif data == 'radm_announceok':
        await _run_announce(query, ctx)
    elif data == 'radm_preview':
        await query.answer('پیش‌نمایش ارسال شد 👇')
        await _send_preview(query, ctx)


# ─── Admin inputs (conversation) ────────────────────────────────────────────────
def _input_prompt(action, arg, values):
    if action in TEXT_INPUTS:
        key, title, limit, hint = TEXT_INPUTS[action]
        current = str(values.get(key) or '')
        return (
            f'✏️ {title}\n\nمقدار فعلی:\n━━━━━━━━━━━━━━━\n{current[:1500]}\n━━━━━━━━━━━━━━━\n\n'
            + (f'{hint}\n' if hint else '')
            + f'حداکثر {limit} کاراکتر. برای برگشت به پیش‌فرض بفرست: پیش‌فرض'
        )
    if action == 'photo':
        return (
            '🖼 یک عکس برای بنر دعوت بفرست (کپشن لازم نیست).\n'
            'برای حذف عکس بنویس: حذف\n\n'
            '⚠️ با عکس، متن بنر حداکثر ۱۰۲۴ کاراکتر می‌تواند باشد؛ اگر بلندتر باشد بدون عکس ارسال می‌شود.'
        )
    if action == 'menulabel':
        current = appearance.user_label('b.menu.ref', appearance.DEFAULTS['b.menu.ref'])
        return (
            f'📱 متن دکمه منوی اصلی\n\nمقدار فعلی: {current}\n\n'
            'متن جدید را بفرست (حداکثر ۶۴ کاراکتر). برای پیش‌فرض بفرست: پیش‌فرض\n'
            'ایموجی پریمیوم این دکمه از «✨ ظاهر ← منوی پایین» قابل تنظیم است.'
        )
    if action == 'newcamp':
        return (
            '🏁 شروع مسابقه جدید\n\n'
            'مدت مسابقه را بفرست:\n'
            '• 7 ← هفت روز\n'
            '• 48h ← ۴۸ ساعت\n\n'
            'می‌توانی عنوان هم بدهی:\n'
            '7 | مسابقه هفته اول مهر\n\n'
            '⚠️ اگر مسابقه‌ای باز است، اول بسته می‌شود و برندگانش ذخیره می‌شوند.\n'
            '✅ امتیازها از همین لحظه از صفر شمرده می‌شوند (سابقه در «همه معرف‌ها» می‌ماند).'
        )
    if action == 'extend':
        return 'زمان باقی‌مانده‌ی مسابقه فعلی را از همین حالا بفرست (مثل 3 برای ۳ روز یا 12h برای ۱۲ ساعت).'
    if action == 'topn':
        return f'تعداد نفرات جدول و برندگان را بفرست (۳ تا ۵۰). مقدار فعلی: {rdb.top_n(values)}'
    if action == 'msg' and arg.isdigit():
        return (
            f'💬 پیام برای کاربر {arg} را بفرست.\n'
            'متن، عکس، ویدیو یا هر پیامی — همان‌طور که می‌فرستی (با فرمت و ایموجی) کپی می‌شود.\n'
            'زیر پیام دکمه‌های «صفحه دعوت من» و «پشتیبانی» اضافه می‌شود.'
        )
    if action == 'msgtop':
        return (
            f'📨 پیام برای {rdb.top_n(values)} نفر برتر مسابقه فعلی را بفرست.\n'
            'هر نوع پیامی قابل ارسال است.'
        )
    if action == 'msgall':
        return '📣 پیام برای همه کاربرانی که حداقل یک دعوت داشته‌اند را بفرست.\nهر نوع پیامی قابل ارسال است.'
    if action == 'msgwin' and arg.isdigit():
        return f'🏆 پیام برای برندگان دوره #{arg} را بفرست.\nهر نوع پیامی قابل ارسال است.'
    return None


async def _recipient_markup():
    rows = [[_btn('🎁 صفحه دعوت من', 'refu_home', 'success')]]
    try:
        support = await asyncio.to_thread(get_support_contact)
        if support.get('url'):
            rows.append([_btn('🎧 پشتیبانی', style='primary', url=support['url'])])
    except Exception:
        _LOG.debug('Support contact unavailable for referral message', exc_info=True)
    return InlineKeyboardMarkup(rows)


async def _copy_to_many(bot, message, recipients):
    markup = await _recipient_markup()
    sent = failed = 0
    unique = list(dict.fromkeys(str(r) for r in recipients if str(r or '').isdigit()))
    for index, tg in enumerate(unique, start=1):
        try:
            await bot.copy_message(
                chat_id=int(tg), from_chat_id=message.chat_id,
                message_id=message.message_id, reply_markup=markup,
            )
            sent += 1
        except Exception:
            failed += 1
        if index % 25 == 0:
            await asyncio.sleep(1)
    return sent, failed


async def _apply_input(update, ctx, action, arg):
    message = update.message
    uid = update.effective_user.id
    raw = (message.text or '').strip() if message.text else ''
    values = await asyncio.to_thread(rdb.settings, True)
    back_texts = [[_btn('🔙 متن‌ها', 'radm_texts')], _home_button()]

    if action in TEXT_INPUTS:
        key, title, limit, _hint = TEXT_INPUTS[action]
        if not raw:
            raise ValueError('فقط متن بفرست.')
        if raw in _DEFAULT_WORDS:
            await asyncio.to_thread(rdb.put, key, '')
            result = f'✅ {title} به پیش‌فرض برگشت.'
        else:
            if len(raw) > limit:
                raise ValueError(f'حداکثر {limit} کاراکتر مجاز است (الان {len(raw)}).')
            if action.startswith('b') and '\n' in raw:
                raise ValueError('متن دکمه باید یک خط باشد.')
            await asyncio.to_thread(rdb.put, key, raw)
            result = f'✅ {title} ذخیره شد.'
        await asyncio.to_thread(log_admin_action, uid, 'referral_text', 'setting', key, 'value changed')
        values = await asyncio.to_thread(rdb.settings, True)
        if action == 'tbanner' and values.get('referral_banner_photo') and len(values[key]) > 1024:
            result += '\n⚠️ متن از ۱۰۲۴ کاراکتر بلندتر است؛ بنر بدون عکس ارسال می‌شود.'
        if action.startswith('t'):
            campaign = await asyncio.to_thread(rdb.active_campaign)
            sample = common_values(values, campaign)
            sample.update(
                name=update.effective_user.first_name or 'علی', inviter='رضا', friend='سا•••',
                link=rdb.referral_link(ctx.bot.username, uid), count='12', total='30',
                bought='5', rank='2',
            )
            await message.reply_text(result + '\n\n👀 پیش‌نمایش:')
            await deliver(ctx.bot, uid, values[key], sample)
            return '👆 همین‌طور برای کاربران نمایش داده می‌شود.', back_texts
        return result, back_texts

    if action == 'photo':
        if message.photo:
            await asyncio.to_thread(rdb.put, 'referral_banner_photo', message.photo[-1].file_id)
            await asyncio.to_thread(log_admin_action, uid, 'referral_banner_photo', 'setting',
                                    'referral_banner_photo', 'set')
            return '✅ عکس بنر ذخیره شد. برای دیدن نتیجه «👀 پیش‌نمایش» را بزن.', back_texts
        if raw == 'حذف':
            await asyncio.to_thread(rdb.put, 'referral_banner_photo', '')
            return '✅ عکس بنر حذف شد.', back_texts
        raise ValueError('یک عکس بفرست یا برای حذف بنویس «حذف».')

    if action == 'menulabel':
        if not raw:
            raise ValueError('فقط متن بفرست.')
        if raw in _DEFAULT_WORDS:
            await asyncio.to_thread(upsert_appearance, 'b.menu.ref', clear_text=True)
        else:
            if len(raw) > appearance.BUTTON_TEXT_MAX or '\n' in raw:
                raise ValueError('متن دکمه باید یک خط و حداکثر ۶۴ کاراکتر باشد.')
            other = appearance.menu_action(raw)
            if other and other != 'referral':
                raise ValueError('این متن با یکی دیگر از دکمه‌های منو یکسان است.')
            await asyncio.to_thread(upsert_appearance, 'b.menu.ref', text=raw)
        appearance.invalidate_cache()
        await asyncio.to_thread(log_admin_action, uid, 'appearance_updated', 'appearance', 'b.menu.ref', 'text')
        return '✅ متن دکمه منو ذخیره شد (با نمایش بعدی منو اعمال می‌شود).', back_texts

    if action == 'newcamp':
        if not raw:
            raise ValueError('مدت مسابقه را بفرست.')
        duration, _sep, title = raw.replace('｜', '|').partition('|')
        hours = parse_duration_hours(duration)
        campaign_id, closed = await asyncio.to_thread(
            rdb.start_campaign, hours, title.strip(), rdb.top_n(values),
        )
        await asyncio.to_thread(log_admin_action, uid, 'referral_campaign_started', 'campaign',
                                campaign_id, f'hours={hours}')
        lines = [f'✅ مسابقه #{campaign_id} شروع شد و امتیازها از صفر شمرده می‌شوند.']
        for old in closed:
            lines.append(f'\n📜 دوره #{old["id"]} بسته و {len(old["winners"])} برنده ذخیره شد:')
            lines.extend(
                f'{medal(w["rank"])} {w["name"]} · {w["telegram_id"]} — {w["count"]}'
                for w in old['winners']
            )
        if not rdb.is_on(values, 'referral_enabled'):
            lines.append('\n⚠️ بخش دعوت هنوز خاموش است؛ از پنل روشنش کن.')
        lines.append('\n📣 پیشنهاد: از «اعلام مسابقه به همه کاربران» خبرش را بده.')
        return '\n'.join(lines), [
            [_btn('📣 اعلام مسابقه به همه کاربران', 'radm_announce', 'success')],
            _home_button(),
        ]

    if action == 'extend':
        hours = parse_duration_hours(raw)
        campaign_id = await asyncio.to_thread(rdb.set_campaign_end, hours)
        if not campaign_id:
            raise ValueError('مسابقه‌ی فعالی نیست؛ اول «شروع مسابقه جدید» را بزن.')
        await asyncio.to_thread(log_admin_action, uid, 'referral_campaign_end', 'campaign',
                                campaign_id, f'hours={hours}')
        return f'✅ زمان پایان مسابقه #{campaign_id} تغییر کرد.', [_home_button()]

    if action == 'topn':
        try:
            count = int(raw.translate(_DIGITS))
        except ValueError:
            raise ValueError('فقط عدد بفرست.') from None
        if not 3 <= count <= 50:
            raise ValueError('عدد باید بین ۳ تا ۵۰ باشد.')
        await asyncio.to_thread(rdb.put, 'referral_top_n', str(count))
        return f'✅ تعداد نفرات جدول و برندگان: {count}', [
            [_btn('🔙 تنظیمات', 'radm_settings')], _home_button(),
        ]

    if action in ('msg', 'msgtop', 'msgall', 'msgwin'):
        if action == 'msg':
            recipients = [arg]
        elif action == 'msgtop':
            campaign = await asyncio.to_thread(rdb.active_campaign)
            top = await asyncio.to_thread(
                rdb.leaderboard, campaign, values['referral_count_mode'], rdb.top_n(values),
            )
            recipients = [entry['telegram_id'] for entry in top]
        elif action == 'msgall':
            recipients = await asyncio.to_thread(rdb.all_referrer_telegram_ids)
        else:
            campaign = await asyncio.to_thread(rdb.get_campaign, int(arg))
            recipients = [w.get('telegram_id') for w in (campaign or {}).get('winners') or []]
        if not recipients:
            raise ValueError('گیرنده‌ای پیدا نشد.')
        status = await message.reply_text(f'⏳ ارسال به {len(recipients):,} نفر…')
        sent, failed = await _copy_to_many(ctx.bot, message, recipients)
        await asyncio.to_thread(log_admin_action, uid, 'referral_message', 'referral',
                                arg or action, f'sent={sent} failed={failed}')
        try:
            await status.delete()
        except Exception:
            pass
        back = [[_btn('🔙 کارت کاربر', f'radm_u_{arg}')]] if action == 'msg' else []
        back.append(_home_button())
        return f'✅ ارسال شد.\nموفق: {sent:,}\nناموفق: {failed:,}', back

    raise ValueError('عملیات نامعتبر است.')


async def referral_admin_input_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _admin_guard(update):
        return ConversationHandler.END
    await query.answer()
    action, _sep, arg = query.data[len('radm_in_'):].partition('_')
    values = await asyncio.to_thread(rdb.settings, True)
    prompt = _input_prompt(action, arg, values)
    if prompt is None:
        return ConversationHandler.END
    ctx.user_data['radm_action'] = (action, arg)
    await _admin_edit(query, ctx.bot, prompt + '\n\n/cancel برای انصراف', [], markdown=False)
    return WAIT_REF_INPUT


async def referral_admin_input_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _admin_guard(update):
        return ConversationHandler.END
    pending = ctx.user_data.get('radm_action')
    message = update.message
    if not pending or message is None:
        return ConversationHandler.END
    action, arg = pending
    if message.text and appearance.menu_action(message.text) and action != 'menulabel':
        ctx.user_data.pop('radm_action', None)
        await message.reply_text(
            'ویرایش پنل دعوت لغو شد؛ دوباره دکمه منو را بزن.', reply_markup=main_menu(),
        )
        return ConversationHandler.END
    try:
        text, rows = await _apply_input(update, ctx, action, arg)
    except ValueError as exc:
        await message.reply_text(f'❌ {exc}\nدوباره بفرست یا /cancel بزن.')
        return WAIT_REF_INPUT
    except Exception as exc:
        _LOG.exception('Referral admin input failed action=%s', action)
        ctx.user_data.pop('radm_action', None)
        await message.reply_text(
            f'❌ عملیات انجام نشد: {exc}', reply_markup=InlineKeyboardMarkup([_home_button()]),
        )
        return ConversationHandler.END
    ctx.user_data.pop('radm_action', None)
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    return ConversationHandler.END


async def referral_admin_input_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('radm_action', None)
    await update.message.reply_text('انصراف.', reply_markup=InlineKeyboardMarkup([_home_button()]))
    return ConversationHandler.END


def referral_admin_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(referral_admin_input_start, pattern=ADMIN_INPUT_PATTERN)],
        states={
            WAIT_REF_INPUT: [
                MessageHandler(
                    filters.UpdateType.MESSAGE & ~filters.COMMAND,
                    referral_admin_input_receive,
                ),
            ],
        },
        fallbacks=[CommandHandler('cancel', referral_admin_input_cancel)],
        allow_reentry=True,
    )
