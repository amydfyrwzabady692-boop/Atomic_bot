"""احراز هویت بعد از پرداخت — همان قاعدهٔ سایت.

پیش از پرداخت: فقط پذیرفتنِ «بازگشت وجه نداریم» (یک‌بار برای هر نسخهٔ قوانین).
خودِ قاعده (سقف و بازه) هیچ‌جا به خریدار گفته نمی‌شود؛ فقط وقتی سفارشی نگه داشته
شد، از همان خریدار مدارک خواسته می‌شود. rule_text فقط برای پنل ادمین است.
بعد از پرداخت: اگر جمع خریدهای پرداخت‌شدهٔ چند روز اخیرِ خریدارِ احرازنشده
به سقف برسد، سفارش «kyc_hold» می‌ماند (db.claim_identity_hold) تا خریدار شماره
و عکس کارت بانکی، و عکس کارت ملی یا شناسنامه کنار یک دست‌نوشتهٔ کوتاه (با تاریخ،
امضا و اثر انگشت) را بفرستد و ادمین تایید کند.
با تایید، سفارش‌های منتظر خودکار تحویل می‌شوند.

احراز سایت و ربات مشترک است: بعد از اشتراکِ شماره، اگر حسابِ سایت با همین شماره
احراز شده باشد، این‌جا هم تایید می‌شود. مدارکِ سایت هم از صفِ اعلان‌ها به
ادمین‌های ربات می‌رسد و با دکمه‌های site_idv_* بررسی می‌شود.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from telegram import (
    ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from admin_notify import admin_ids, is_admin, notify_admin
from db import (
    LEVEL_PHOTOS, LEVEL_VIDEO, accept_terms, claim_identity_hold_notice, count_identity_queue,
    get_identity_verification, get_or_create_user, get_order, identity_card_owned_by_other,
    identity_level, identity_state, identity_threshold_toman, identity_video_threshold_toman,
    identity_what_to_ask, identity_window_days, import_site_identity, list_identity_held_orders,
    list_pending_identity_verifications, reject_identity_hold_order, release_identity_hold_order,
    release_identity_holds, review_identity_verification, submit_identity_verification,
    terms_accepted,
)
from keyboards import main_menu
from review_broadcast import admin_display_name, broadcast_reviewed, remember_notices, review_footer

log = logging.getLogger(__name__)

SAMPLES = Path(__file__).resolve().parent.parent / 'assets' / 'identity_samples'
# file_idِ نمونه‌ها بعد از اولین ارسال؛ آپلودِ دوباره لازم نیست.
_SAMPLE_IDS: dict[str, str] = {}

WAIT_PHONE, WAIT_NUMBER, WAIT_CARD, WAIT_NATIONAL, WAIT_VIDEO = range(5)
# ویدیوی ۳۰ ثانیه‌ای؛ تا یک دقیقه هم پذیرفته می‌شود.
VIDEO_MAX_SECONDS = 30

REJECT_REASONS = {
    'blur': 'عکس‌ها تار یا ناخوانا است',
    'name': 'نام کارت بانکی با کارت ملی یا شناسنامه یکی نیست',
    'note': 'دست‌نوشته ناقص است (متن، تاریخ، امضا یا اثر انگشت)',
    'card': 'شمارهٔ کارت با عکس کارت یکی نیست',
    'other': 'مدارک ناقص یا نامعتبر است',
}

_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
_REVIEW_RE = re.compile(r'^(site_idv|idv)_(ok|no|rj|back|why)_(\d+)(?:_([a-z]+))?$')
# ردِ سفارشِ منتظرِ احراز (ربات و سایت) — دلیل برای خریدار فرستاده می‌شود.
HOLD_REJECT_REASONS = {
    'card': 'کارت پرداخت متعلق به خریدار نیست',
    'fake': 'مدارک جعلی یا دستکاری‌شده است',
    'none': 'خریدار احراز هویت را کامل نکرد',
    'other': 'سفارش پس از بررسی رد شد',
}
# ادمینی که «نوشتن دلیل» را زده: پیامِ متنیِ بعدی‌اش دلیلِ رد است.
# {آیدی ادمین: {prefix, id, chat_id, message_id, photo, at}}
_PENDING_REASON: dict[int, dict] = {}
REASON_TTL = 15 * 60


def fa(value) -> str:
    return str(value).translate(_FA)


def fa_money(amount) -> str:
    return f'{int(amount or 0):,}'.replace(',', '٬').translate(_FA)


def rule_text() -> str:
    """فقط برای ادمین‌ها — خریدار قاعده را نمی‌بیند."""
    return (f'سطح ۱ (عکس مدارک): خرید یا شارژِ اخیر از {fa_money(identity_threshold_toman())} '
            f'تومان · سطح ۲ (عکس + ویدیو): از {fa_money(identity_video_threshold_toman())} تومان. '
            f'خریدها در {fa(identity_window_days())} روز جمع می‌شوند.')


LEVEL_LABELS = {LEVEL_PHOTOS: 'عکس مدارک', LEVEL_VIDEO: 'عکس مدارک + ویدیو'}


def needs_sentence(ask) -> str:
    """چه چیزی از خریدار خواسته می‌شود — بدون هیچ حرفی از مبلغ و سقف (مثل سایت)."""
    if ask.get('photos') and ask.get('video'):
        return ('عکس کارت بانکی، عکس کارت ملی یا شناسنامه کنار یک دست‌نوشتهٔ کوتاه، و یک '
                'ویدیوی کوتاه از خودت')
    if ask.get('video'):
        return 'یک ویدیوی کوتاه از خودت (عکس‌های مدارکت را قبلاً فرستاده‌ای)'
    return 'عکس کارت بانکی، و عکس کارت ملی یا شناسنامه کنار یک دست‌نوشتهٔ کوتاه'


def video_sentence(day=None) -> str:
    return (f'من [نام و نام خانوادگی]، امروز {note_date(day)}، برای خرید از اتومیک شاپ '
            'احراز هویت می‌کنم و این خرید را خودم انجام داده‌ام.')


# دست‌نوشته‌ای که با تاریخِ چند روز پیش‌تر نوشته شده هنوز پذیرفته است (مثل سایت).
NOTE_MAX_AGE_DAYS = 3
_TEHRAN_OFFSET = timezone(timedelta(hours=3, minutes=30))  # ایران از ۱۴۰۱ ساعت تابستانی ندارد


def _tehran_day(moment=None):
    moment = moment or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo('Asia/Tehran')).date()
    except Exception:  # noqa: BLE001 — بدون tzdata
        return moment.astimezone(_TEHRAN_OFFSET).date()


def note_date(day=None) -> str:
    """تاریخ شمسیِ یک روز (پیش‌فرض امروزِ تهران)، مثل ۱۴۰۵/۰۸/۰۸."""
    from handlers.order_track import to_jalali

    day = day or _tehran_day()
    jy, jm, jd = to_jalali(day.year, day.month, day.day)
    return fa(f'{jy}/{jm:02d}/{jd:02d}')


def note_lines(day=None):
    """متنی که خریدار روی کاغذ می‌نویسد و کنار کارت ملی عکس می‌گیرد — همان سایت."""
    return (
        'اینجانب [نام و نام خانوادگی] این مدارک را فقط برای احراز هویت در اتومیک شاپ می‌فرستم.',
        f'تاریخ: {note_date(day)} — امضا و اثر انگشت',
    )


def review_checklist(sent_at=None):
    """آنچه بررسی‌کننده باید در عکس‌ها ببیند؛ بازهٔ تاریخ از زمانِ ارسال مدارک."""
    sent = _tehran_day(sent_at)
    oldest, newest = note_date(sent - timedelta(days=NOTE_MAX_AGE_DAYS)), note_date(sent)
    return [
        'نام روی کارت بانکی، کارت ملی یا شناسنامه و دست‌نوشته یکی باشد.',
        f'دست‌نوشته: متن احراز، امضا، اثر انگشت و تاریخی از {oldest} تا {newest}.',
    ]


def _masked(number) -> str:
    number = str(number or '')
    return f'{number[:4]}-{number[4:6]}**-****-{number[12:]}' if len(number) == 16 else number


def _pretty(number) -> str:
    number = str(number or '')
    return '-'.join(number[i:i + 4] for i in range(0, len(number), 4))


# ── پیش از پرداخت: بازگشت وجه نداریم ─────────────────────────────────


def terms_text() -> str:
    return (
        '📜 *پیش از پرداخت*\n'
        '━━━━━━━━━━━━━━━\n'
        '• *بازگشت وجه نداریم:* جم، شارژ، گیفت کارت و اکانت کالای دیجیتال‌اند و بعد از '
        'تحویل پس گرفته نمی‌شوند. از آیدی، بسته و مبلغ مطمئن شو.\n'
        '• فقط اگر تحویل از طرف ما انجام نشود، مبلغ به کیف پولت برمی‌گردد.\n\n'
        'اگر قبول داری، دکمهٔ زیر را بزن.'
    )


def terms_keyboard(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ قبول دارم و از خریدم مطمئنم',
                              callback_data=f'terms_ok_{int(order_id)}')],
        [InlineKeyboardButton('❌ انصراف', callback_data=f'cancel_order_{int(order_id)}')],
    ])


async def terms_gate(query, telegram_id, order_id) -> bool:
    """True یعنی قوانین پذیرفته شده؛ وگرنه متنش با دکمهٔ «قبول دارم» نشان داده می‌شود."""
    if await asyncio.to_thread(terms_accepted, telegram_id):
        return True
    await query.edit_message_text(terms_text(), parse_mode='Markdown',
                                  reply_markup=terms_keyboard(order_id))
    return False


async def terms_accept(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer('ثبت شد')
    try:
        order_id = int(query.data.rsplit('_', 1)[-1])
    except ValueError:
        return
    user = update.effective_user
    await asyncio.to_thread(get_or_create_user, user.id, user.first_name or '',
                            user.last_name or '', user.username or '')
    await asyncio.to_thread(accept_terms, user.id)
    from handlers.payment import _order_pay_keyboard

    await query.edit_message_text(
        f'✅ قوانین پذیرفته شد.\nروش پرداخت سفارش #{order_id} را انتخاب کن:',
        reply_markup=_order_pay_keyboard(order_id, ctx.user_data.get('db_id')))


# ── بعد از پرداخت: سفارشِ منتظرِ احراز ──────────────────────────────


def start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🪪 شروع احراز هویت', callback_data='idv_start')],
    ])


def hold_user_text(order_id, state, ask=None) -> str:
    # سقف و بازه به خریدار گفته نمی‌شود؛ فقط همان مدرکی که کم دارد.
    head = f'✅ پرداخت سفارش #{order_id} انجام شد.\n\n'
    if state == 'pending':
        return head + '⏳ مدارک احراز هویتت در صف بررسی است؛ بعد از تایید، سفارش خودکار تحویل می‌شود.'
    ask = ask or {'photos': True, 'video': False}
    return head + (f'برای تکمیل این سفارش احراز هویت لازم است: {needs_sentence(ask)}.\n'
                   'پولت محفوظ است و موجودی سفارش برایت کنار گذاشته شده.')


def held_order_admin_keyboard(order_id, prefix='idva'):
    """سه تصمیم برای سفارشِ منتظرِ احراز؛ prefix=skyc برای سفارش‌های سایت."""
    oid = int(order_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('▶️ آزادسازی بدون احراز', callback_data=f'{prefix}_rel_{oid}')],
        [InlineKeyboardButton('↩️ رد + برگشت پول به کیف پول', callback_data=f'{prefix}_ref_{oid}')],
        [InlineKeyboardButton('⛔ رد بدون بازگشت وجه', callback_data=f'{prefix}_keep_{oid}')],
    ])


async def announce_identity_hold(bot, order_id, *, tg_id=None, edit_message=None):
    """«منتظر احراز هویت» به خریدار و ادمین؛ پیام کامل فقط یک‌بار برای هر سفارش."""
    first = await asyncio.to_thread(claim_identity_hold_notice, order_id)
    order = await asyncio.to_thread(get_order, order_id)
    tg = tg_id or (order[6] if order else None)
    state = await asyncio.to_thread(identity_state, tg) if tg else 'none'
    ask = await asyncio.to_thread(identity_what_to_ask, tg) if tg else {'photos': True,
                                                                       'video': False, 'level': 1}
    if edit_message:
        try:
            await edit_message(f'✅ پرداخت سفارش #{order_id} انجام شد — منتظر احراز هویت.\n'
                               'پیام بعدی را ببین.')
        except Exception:  # noqa: BLE001
            pass
    if tg and (first or edit_message):
        try:
            await bot.send_message(chat_id=int(tg), text=hold_user_text(order_id, state, ask),
                                   reply_markup=None if state == 'pending' else start_keyboard())
        except Exception as exc:  # noqa: BLE001 — خریدار ربات را بسته
            log.warning('identity hold notice not delivered tg=%s order=%s: %s', tg, order_id, exc)
    if first:
        label = {'pending': 'مدارک در صف بررسی', 'rejected': 'رد شده؛ منتظر ارسال دوباره'}.get(
            state, 'هنوز مدارکی نفرستاده')
        await notify_admin(bot, (
            f'⏸ سفارش #{order_id} منتظر احراز هویت\n'
            f'مبلغ: {int(order[2] or 0) if order else 0:,} تومان · کاربر: {tg or "—"}\n'
            f'وضعیت احراز: {label}\n'
            f'مدرک لازم: {LEVEL_LABELS.get(int(ask.get("level") or 1), "عکس مدارک")}\n\n'
            'پول رسیده؛ تا تایید مدارک چیزی تحویل نده. با تایید احراز، تحویل خودکار است.'),
            parse_mode=None, reply_markup=held_order_admin_keyboard(order_id))
    return state


async def release_and_deliver(bot, telegram_id, *, order_ids=None):
    """سفارش‌های منتظر را آزاد و تحویل می‌کند. خروجی: [order_id]."""
    from handlers.payment import _handle_fulfill_result, fulfill_order_async

    if order_ids is None:
        order_ids = await asyncio.to_thread(release_identity_holds, telegram_id)
    for order_id in order_ids:
        try:
            success, status = await fulfill_order_async(order_id)
            await _handle_fulfill_result(bot, order_id, success, status, tg_id=telegram_id,
                                         payment_hint='kyc_release')
        except Exception:  # noqa: BLE001 — حلقهٔ پیگیری دوباره تلاش می‌کند
            log.exception('delivery after identity release failed order=%s', order_id)
    return list(order_ids)


# ── گفتگوی احراز ─────────────────────────────────────────────────────


def _cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton('❌ انصراف', callback_data='idv_cancel')]])


def _intro_text(with_video=False) -> str:
    steps = 4 if with_video else 3
    return (
        '🪪 *احراز هویت*\n'
        '━━━━━━━━━━━━━━━\n'
        'برای تکمیل سفارشت احراز هویت لازم است:\n'
        '۱. شمارهٔ موبایلِ همین حساب تلگرام\n'
        '۲. شماره و عکس کارت بانکی\n'
        '۳. کارت ملی یا شناسنامه کنار یک دست‌نوشته\n'
        + ('۴. یک ویدیوی کوتاه از خودت\n' if with_video else '')
        + '\n🔒 مدارک فقط برای مدیران فروشگاه فرستاده می‌شود.\n\n'
        f'📱 *قدم ۱ از {fa(steps)}* — دکمهٔ «اشتراک شماره» پایین صفحه را بزن.'
    )


def _video_intro_text(only=False) -> str:
    # Markdownِ قدیمی «[» را شروعِ لینک می‌داند.
    sentence = video_sentence().replace('[', '\\[')
    head = ('🎥 *یک قدم مانده — ویدیوی کوتاه احراز*\n' if only
            else '🎥 *قدم آخر — ویدیوی کوتاه احراز*\n')
    return (head
            + f'با دوربین جلوی گوشی یک ویدیوی حداکثر {fa(VIDEO_MAX_SECONDS)} ثانیه‌ای از صورتت بگیر. '
            'کارت ملی و کارت بانکی را کنار صورتت نگه دار تا خوانا دیده شوند و این جمله را بلند '
            f'بخوان:\n\n«{sentence}»\n\n'
            'ویدیو را همین‌جا بفرست (ویدیوی معمولی یا ویدیوی دایره‌ای تلگرام).')


async def _left_for_menu(update, ctx) -> bool:
    """دکمهٔ منوی پایین وسطِ احراز: گفتگو تمام می‌شود تا خریدار گیر نکند."""
    text = getattr(update.message, 'text', None) if update.message else None
    if not text:
        return False
    try:
        import appearance

        if not appearance.menu_action(text):
            return False
    except Exception:  # noqa: BLE001
        return False
    ctx.user_data.pop('idv', None)
    await update.message.reply_text('احراز هویت متوقف شد؛ دوباره دکمهٔ منو را بزن 👇\n'
                                    'هر وقت خواستی با /kyc ادامه بده.', reply_markup=main_menu())
    return True


async def _reply(update, text, **kwargs):
    message = update.effective_message
    return await message.reply_text(text, **kwargs)


async def idv_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user = update.effective_user
    db_id, _ = await asyncio.to_thread(get_or_create_user, user.id, user.first_name or '',
                                       user.last_name or '', user.username or '')
    ctx.user_data['db_id'] = db_id
    state = await asyncio.to_thread(identity_state, user.id)
    if state == 'approved':
        released = await release_and_deliver(ctx.bot, user.id)
        await _reply(update, '✅ احراز هویتت تایید شده است.'
                     + (f'\n{fa(len(released))} سفارش منتظر در حال تحویل است.' if released else ''),
                     reply_markup=main_menu())
        return ConversationHandler.END
    if state in ('none', 'rejected') and not await asyncio.to_thread(
            list_identity_held_orders, user.id, 1):
        # فقط وقتی سفارشی منتظر است مدارک خواسته می‌شود.
        await _reply(update, 'فعلاً نیازی به احراز هویت نداری.', reply_markup=main_menu())
        return ConversationHandler.END
    resend = bool(query and query.data == 'idv_resend')
    ask = await asyncio.to_thread(identity_what_to_ask, user.id)
    pending_row = None
    if state == 'pending':
        from db import get_identity_verification_by_tg

        pending_row = await asyncio.to_thread(get_identity_verification_by_tg, user.id)
    # عکس‌ها قبلاً تایید شده یا در صف است و فقط ویدیو کم است: مستقیم قدمِ ویدیو.
    video_missing = ask['video'] and not (pending_row and pending_row[15])
    if video_missing and (not ask['photos'] or pending_row):
        ctx.user_data['idv'] = {'video_only': True}
        await _reply(update, _video_intro_text(only=True), parse_mode='Markdown',
                     reply_markup=_cancel_kb())
        return WAIT_VIDEO
    if state == 'pending' and not resend:
        await _reply(update, '⏳ مدارکت در صف بررسی است. بعد از تایید همین‌جا خبرت می‌کنیم.',
                     reply_markup=InlineKeyboardMarkup([
                         [InlineKeyboardButton('📤 ارسال دوبارهٔ مدارک', callback_data='idv_resend')],
                         [InlineKeyboardButton('🏠 منوی اصلی', callback_data='home')],
                     ]))
        return ConversationHandler.END
    ctx.user_data['idv'] = {'with_video': bool(ask['video'])}
    await _reply(update, _intro_text(with_video=bool(ask['video'])), parse_mode='Markdown')
    await _reply(update, 'برای ادامه دکمهٔ زیر را بزن 👇', reply_markup=ReplyKeyboardMarkup(
        [[KeyboardButton('📱 اشتراک شماره موبایل', request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True))
    return WAIT_PHONE


async def idv_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from handlers.card_verify import normalize_phone

    if await _left_for_menu(update, ctx):
        return ConversationHandler.END
    contact = update.message.contact
    if contact is None or contact.user_id != update.effective_user.id:
        await update.message.reply_text(
            '❌ فقط شمارهٔ *خودِ همین حساب* پذیرفته می‌شود؛ دکمهٔ «اشتراک شماره» را بزن.',
            parse_mode='Markdown')
        return WAIT_PHONE
    phone = normalize_phone(contact.phone_number)
    if not (phone.startswith('09') and len(phone) == 11):
        await update.message.reply_text('❌ فقط شمارهٔ موبایل ایران (۰۹…) پذیرفته می‌شود.',
                                        reply_markup=ReplyKeyboardRemove())
        ctx.user_data.pop('idv', None)
        return ConversationHandler.END
    ctx.user_data.setdefault('idv', {})['phone'] = phone

    # احراز مشترک با سایت: تلگرام تضمین کرده شماره مالِ همین حساب است.
    if await _import_from_site(update, ctx, phone):
        return ConversationHandler.END

    await update.message.reply_text(
        f'✅ شماره {phone} ثبت شد.\n\n💳 *قدم ۲ از ۳ — کارت بانکی*\n'
        'شمارهٔ ۱۶ رقمیِ کارت بانکی‌ات را بفرست.',
        parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())
    return WAIT_NUMBER


async def _import_from_site(update, ctx, phone) -> bool:
    user = update.effective_user
    try:
        from site_api import fetch_site_identity

        data = await asyncio.to_thread(fetch_site_identity, phone, user.id)
    except Exception:  # noqa: BLE001 — سایت در دسترس نیست: احراز معمولی ادامه دارد
        log.exception('site identity lookup failed tg=%s', user.id)
        return False
    if not data.get('approved'):
        return False
    if not await asyncio.to_thread(import_site_identity, user.id, data.get('card_number'), phone,
                                   int(data.get('level') or 1)):
        log.warning('site identity for tg=%s not imported: card is on another account', user.id)
        return False
    ctx.user_data.pop('idv', None)
    released = await release_and_deliver(ctx.bot, user.id)
    await update.message.reply_text(
        '✅ احراز هویتت در سایت اتومیک شاپ تایید شده بود؛ این‌جا هم فعال شد.'
        + (f'\n{fa(len(released))} سفارش منتظر در حال تحویل است.' if released else
           '\nاز این به بعد خریدهایت بدون انتظار تحویل می‌شوند.'),
        reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text('منوی اصلی:', reply_markup=main_menu())
    return True


async def _send_sample(message, name, caption):
    """نمونهٔ عکس — از file_idِ قبلی، یا آپلود از assets/identity_samples."""
    try:
        file_id = _SAMPLE_IDS.get(name)
        if file_id:
            await message.reply_photo(photo=file_id, caption=caption, parse_mode='Markdown',
                                      reply_markup=_cancel_kb())
            return
        with open(SAMPLES / f'{name}.png', 'rb') as handle:
            sent = await message.reply_photo(photo=handle, caption=caption, parse_mode='Markdown',
                                             reply_markup=_cancel_kb())
        if sent.photo:
            _SAMPLE_IDS[name] = sent.photo[-1].file_id
    except Exception:  # noqa: BLE001 — بدون نمونه هم ادامه بده
        log.exception('identity sample %s could not be sent', name)
        _SAMPLE_IDS.pop(name, None)
        await message.reply_text(caption, parse_mode='Markdown', reply_markup=_cancel_kb())


CARD_CAPTION = '📷 *عکس کارت بانکی* — مثل نمونه'


def national_caption(day=None) -> str:
    """قدمِ آخر: کارت ملی یا شناسنامه کنار دست‌نوشته، با تاریخِ امروز در متن."""
    # Markdownِ قدیمی «[» را شروعِ لینک می‌داند.
    first, second = (line.replace('[', '\\[') for line in note_lines(day))
    return ('🪪 *قدم ۳ از ۳ — کارت ملی یا شناسنامه کنار دست‌نوشته* — مثل نمونه\n'
            'این متن را روی کاغذ بنویس، امضا کن و اثر انگشت بزن؛ کارت ملی یا شناسنامه را '
            'کنارش بگذار و از هر دو یک عکس بگیر:\n\n'
            f'«{first}\n{second}»')


async def idv_number(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from handlers.card_verify import luhn_ok, normalize_card

    if await _left_for_menu(update, ctx):
        return ConversationHandler.END
    number = normalize_card(update.message.text)
    if len(number) != 16 or not luhn_ok(number):
        await update.message.reply_text('❌ شماره کارت معتبر نیست؛ ۱۶ رقم را با دقت بفرست.',
                                        reply_markup=_cancel_kb())
        return WAIT_NUMBER
    if await asyncio.to_thread(identity_card_owned_by_other, update.effective_user.id, number):
        log.warning('identity card %s…%s offered by tg=%s is on another account',
                    number[:6], number[-4:], update.effective_user.id)
        await update.message.reply_text(
            '❌ این کارت روی حساب دیگری ثبت شده است. اگر کارت مال خودت است به پشتیبانی پیام بده.',
            reply_markup=main_menu())
        ctx.user_data.pop('idv', None)
        return ConversationHandler.END
    ctx.user_data.setdefault('idv', {})['number'] = number
    await _send_sample(update.message, 'bank_card', CARD_CAPTION)
    return WAIT_CARD


async def _photo_or_retry(update):
    message = update.message
    if message.photo:
        return message.photo[-1].file_id
    if message.document and str(message.document.mime_type or '').startswith('image/'):
        await message.reply_text('📷 لطفاً همین را *به‌صورت عکس* بفرست، نه فایل '
                                 '(در گالری عکس را انتخاب کن، نه «فایل»).', parse_mode='Markdown',
                                 reply_markup=_cancel_kb())
    else:
        await message.reply_text('لطفاً *عکس* بفرست.', parse_mode='Markdown',
                                 reply_markup=_cancel_kb())
    return None


async def idv_card_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _left_for_menu(update, ctx):
        return ConversationHandler.END
    file_id = await _photo_or_retry(update)
    if not file_id:
        return WAIT_CARD
    ctx.user_data.setdefault('idv', {})['card'] = file_id
    await _send_sample(update.message, 'national_note', national_caption())
    return WAIT_NATIONAL


def review_keyboard(verification_id, prefix='idv'):
    vid = int(verification_id)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ تایید احراز', callback_data=f'{prefix}_ok_{vid}'),
        InlineKeyboardButton('❌ رد احراز', callback_data=f'{prefix}_no_{vid}'),
    ]])


def reasons_keyboard(verification_id, prefix='idv'):
    vid = int(verification_id)
    rows = [[InlineKeyboardButton(f'❌ {label}', callback_data=f'{prefix}_rj_{vid}_{code}')]
            for code, label in REJECT_REASONS.items()]
    rows.append([InlineKeyboardButton('✍️ نوشتن دلیل رد', callback_data=f'{prefix}_why_{vid}')])
    rows.append([InlineKeyboardButton('↩️ بازگشت', callback_data=f'{prefix}_back_{vid}')])
    return InlineKeyboardMarkup(rows)


def admin_caption(verification_id, user, number, phone, held, other_account,
                  sent_at=None, legacy_selfie=False, level=1, video_only=False) -> str:
    uname = f'@{user.username}' if getattr(user, 'username', None) else '—'
    held_total = sum(max(0, int(row[2] or 0) - int(row[3] or 0)) for row in held)
    level = int(level or 1)
    lines = [
        f'🪪 احراز هویت #{verification_id} — ربات'
        + (' · سطح ۲ (با ویدیو)' if level >= LEVEL_VIDEO else ''),
        '━━━━━━━━━━━━━━━',
        f'شماره کارت: {_pretty(number)}',
        f'📞 موبایلِ حساب تلگرام: {phone or "—"}',
        f'کاربر: {getattr(user, "full_name", "") or "—"} ({uname}) · {user.id}',
    ]
    if held:
        lines.append(f'⏸ سفارش منتظر: {len(held)} (جمع {held_total:,} تومان)')
    if other_account:
        lines.append('⚠️ این کارت روی حساب دیگری هم ثبت شده است.')
    lines += ['', 'عکس‌ها به ترتیب: کارت بانکی، کارت ملی یا شناسنامه کنار دست‌نوشته'
              + ('، سلفی (مدارک قدیمی).' if legacy_selfie else '.')]
    if video_only:
        lines.append('عکس‌ها قبلاً تایید شده‌اند؛ این بار فقط ویدیو آمده.')
    lines += [f'✓ {line}' for line in review_checklist(sent_at)]
    if level >= LEVEL_VIDEO:
        lines.append('✓ ویدیو: همان صورتِ کارت ملی، هر دو کارت خوانا کنار صورت، و جملهٔ احراز '
                     'با تاریخِ همین روزها بلند خوانده شود.')
    lines.append('با تایید، سفارش‌های منتظرِ این خریدار خودکار تحویل می‌شوند.')
    return '\n'.join(lines)


async def send_for_review(bot, verification_id, caption, file_ids, recipients=None,
                          video_file_id=''):
    """آلبوم عکس‌ها، ویدیو (اگر هست) + متن و دکمه‌ها برای هر ادمین. خروجی: notices."""
    media = [InputMediaPhoto(media=file_id) for file_id in file_ids if file_id]
    notices = []
    for aid in (recipients if recipients is not None else admin_ids()):
        try:
            if len(media) > 1:
                await bot.send_media_group(chat_id=aid, media=media)
            elif media:
                await bot.send_photo(chat_id=aid, photo=media[0].media)
            if video_file_id:
                try:
                    await bot.send_video(chat_id=aid, video=video_file_id,
                                         caption=f'🎥 ویدیوی احراز #{int(verification_id)}')
                except BadRequest:
                    # ویدیوی دایره‌ای با sendVideo فرستاده نمی‌شود.
                    await bot.send_video_note(chat_id=aid, video_note=video_file_id)
            msg = await bot.send_message(chat_id=aid, text=caption,
                                         reply_markup=review_keyboard(verification_id))
            notices.append({'chat_id': int(aid), 'message_id': msg.message_id})
        except Exception as exc:  # noqa: BLE001
            log.warning('identity review notice failed admin=%s: %s', aid, exc)
    remember_notices('bot_idv', verification_id, notices)
    return notices


async def idv_national(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدمِ آخر: عکسِ کارت ملی یا شناسنامه کنار دست‌نوشته؛ بعدش مدارک برای بررسی می‌رود."""
    if await _left_for_menu(update, ctx):
        return ConversationHandler.END
    file_id = await _photo_or_retry(update)
    if not file_id:
        return WAIT_NATIONAL
    data = ctx.user_data.get('idv') or {}
    data['national'] = file_id
    if not all(data.get(k) for k in ('phone', 'number', 'card', 'national')):
        ctx.user_data.pop('idv', None)
        await update.message.reply_text('اطلاعات ناقص ماند؛ از اول شروع کن.',
                                        reply_markup=start_keyboard())
        return ConversationHandler.END
    if data.get('with_video'):
        await update.message.reply_text(_video_intro_text(), parse_mode='Markdown',
                                        reply_markup=_cancel_kb())
        return WAIT_VIDEO
    return await _submit_documents(update, ctx, data)


def _video_file_id(message):
    """file_idِ ویدیو، ویدیوی دایره‌ای یا فایلِ ویدیویی؛ وگرنه None."""
    if message.video:
        return message.video.file_id
    if message.video_note:
        return message.video_note.file_id
    document = message.document
    if document and str(document.mime_type or '').startswith('video/'):
        return document.file_id
    return None


async def idv_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدمِ ویدیو: بعدش مدارک (با ویدیو) برای بررسی می‌رود."""
    if await _left_for_menu(update, ctx):
        return ConversationHandler.END
    message = update.message
    file_id = _video_file_id(message)
    if not file_id:
        await message.reply_text('🎥 لطفاً *ویدیو* بفرست؛ با دوربین جلوی گوشی فیلم بگیر.',
                                 parse_mode='Markdown', reply_markup=_cancel_kb())
        return WAIT_VIDEO
    duration = getattr(message.video or message.video_note, 'duration', 0) or 0
    if duration and duration > VIDEO_MAX_SECONDS * 2:
        await message.reply_text(f'ویدیو طولانی است؛ یک ویدیوی حداکثر {fa(VIDEO_MAX_SECONDS)} '
                                 'ثانیه‌ای بفرست.', reply_markup=_cancel_kb())
        return WAIT_VIDEO
    data = ctx.user_data.get('idv') or {}
    data['video'] = file_id
    if data.get('video_only'):
        from db import get_identity_verification_by_tg

        row = await asyncio.to_thread(get_identity_verification_by_tg, update.effective_user.id)
        data.update({'number': (row[2] if row else '') or '', 'phone': (row[3] if row else '') or '',
                     'card': '', 'national': ''})
    return await _submit_documents(update, ctx, data)


async def _submit_documents(update, ctx, data):
    user = update.effective_user
    video = data.get('video') or ''
    verification_id, status = await asyncio.to_thread(
        submit_identity_verification, user.id, data.get('number') or '', data.get('phone') or '',
        data.get('card') or '', data.get('national') or '', '', video,
        LEVEL_VIDEO if video else LEVEL_PHOTOS)
    ctx.user_data.pop('idv', None)
    if status == 'approved':
        released = await release_and_deliver(ctx.bot, user.id)
        await update.message.reply_text(
            '✅ احراز هویتت قبلاً تایید شده است.'
            + (f'\n{fa(len(released))} سفارش منتظر در حال تحویل است.' if released else ''),
            reply_markup=main_menu())
        return ConversationHandler.END
    held = await asyncio.to_thread(list_identity_held_orders, user.id, 20)
    row = await asyncio.to_thread(get_identity_verification, verification_id)
    number = (row[2] if row else '') or data.get('number') or ''
    other = await asyncio.to_thread(identity_card_owned_by_other, user.id, number) if number else False
    level = int(row[13] or 1) if row else (LEVEL_VIDEO if video else LEVEL_PHOTOS)
    photos = [row[4], row[5]] if row else [data.get('card'), data.get('national')]
    caption = admin_caption(verification_id, user, number, row[3] if row else data.get('phone'),
                            held, other, level=level, video_only=bool(data.get('video_only')))
    await send_for_review(ctx.bot, verification_id, caption, photos,
                          video_file_id=(row[15] if row else video) or video)
    await update.message.reply_text(
        '✅ مدارکت ثبت شد و در صف بررسی است. بعد از تایید همین‌جا خبرت می‌کنیم'
        + (' و سفارش‌های منتظرت خودکار تحویل می‌شوند.' if held else '.'),
        reply_markup=main_menu())
    return ConversationHandler.END


async def idv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('idv', None)
    if update.callback_query:
        await update.callback_query.answer('لغو شد')
        await update.callback_query.message.reply_text('احراز هویت لغو شد.',
                                                       reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text('احراز هویت لغو شد.', reply_markup=ReplyKeyboardRemove())
    await update.effective_message.reply_text(
        'هر وقت آماده بودی دوباره شروع کن.', reply_markup=start_keyboard())
    return ConversationHandler.END


def identity_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(idv_start, pattern=r'^idv_(?:start|resend)$'),
            CommandHandler('kyc', idv_start),
        ],
        states={
            WAIT_PHONE: [MessageHandler(filters.CONTACT, idv_phone),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, idv_phone)],
            WAIT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, idv_number)],
            WAIT_CARD: [MessageHandler(filters.ALL & ~filters.COMMAND, idv_card_photo)],
            WAIT_NATIONAL: [MessageHandler(filters.ALL & ~filters.COMMAND, idv_national)],
            WAIT_VIDEO: [MessageHandler(filters.ALL & ~filters.COMMAND, idv_video)],
        },
        fallbacks=[
            CommandHandler('cancel', idv_cancel),
            CallbackQueryHandler(idv_cancel, pattern=r'^idv_cancel$'),
        ],
        allow_reentry=True,
    )


# ── تصمیم ادمین (مدارکِ ربات و سایت) ─────────────────────────────────


async def _edit_decided(query, text):
    try:
        if query.message and (query.message.photo or query.message.document):
            await query.edit_message_caption(caption=text, reply_markup=None)
        else:
            await query.edit_message_text(text=text, reply_markup=None)
    except Exception:  # noqa: BLE001 — همان پیام قبلاً ویرایش شده
        pass


async def identity_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """idv_* برای مدارکِ ربات، site_idv_* برای مدارکِ سایت."""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    match = _REVIEW_RE.match(str(query.data or ''))
    if not match:
        await query.answer('دادهٔ نامعتبر', show_alert=True)
        return
    prefix, action, raw_id, code = match.groups()
    verification_id = int(raw_id)
    if action in ('no', 'back'):
        await query.answer()
        keyboard = (reasons_keyboard(verification_id, prefix) if action == 'no'
                    else review_keyboard(verification_id, prefix))
        try:
            await query.edit_message_reply_markup(reply_markup=_keep_links(query, keyboard))
        except Exception:  # noqa: BLE001
            pass
        return
    if action == 'why':
        message = query.message
        _PENDING_REASON[update.effective_user.id] = {
            'prefix': prefix, 'id': verification_id, 'at': time.time(),
            'chat_id': message.chat_id if message else None,
            'message_id': message.message_id if message else None,
            'photo': bool(message and (message.photo or message.document)),
        }
        await query.answer()
        await ctx.bot.send_message(
            chat_id=update.effective_user.id,
            text=(f'✍️ دلیل رد احراز #{verification_id} را بنویس و بفرست؛ همین متن برای '
                  'خریدار فرستاده می‌شود تا مشکل را درست کند.\nبرای انصراف بنویس: انصراف'),
            reply_markup=ForceReply(selective=True, input_field_placeholder='دلیل رد…'))
        return
    approve = action == 'ok'
    reason = '' if approve else REJECT_REASONS.get(code or 'other', REJECT_REASONS['other'])
    name = admin_display_name(update.effective_user)
    if prefix == 'site_idv':
        await _site_review(query, ctx, verification_id, approve, reason, name)
    else:
        await _bot_review(query, ctx, verification_id, approve, reason, name)


def _keep_links(query, keyboard):
    """ردیف‌های دکمهٔ لینک (دیدنِ عکس‌ها) با عوض شدنِ دکمه‌ها از بین نروند."""
    current = getattr(getattr(query, 'message', None), 'reply_markup', None)
    links = [list(row) for row in (getattr(current, 'inline_keyboard', None) or [])
             if row and all(getattr(button, 'url', None) for button in row)]
    if not links:
        return keyboard
    return InlineKeyboardMarkup([list(row) for row in keyboard.inline_keyboard] + links)


class _TypedReason:
    """دلیلِ نوشته‌شده جای کلیک: همان کارهای CallbackQuery روی پیامِ بررسیِ ذخیره‌شده."""

    def __init__(self, bot, message, pending):
        self._bot = bot
        self._reply_to = message
        self._pending = pending
        self.from_user = message.from_user
        self.message = SimpleNamespace(photo=pending.get('photo'), document=None,
                                       chat_id=pending.get('chat_id'))

    async def answer(self, text='', show_alert=False):
        if text:
            await self._reply_to.reply_text(text)

    async def edit_message_text(self, text, reply_markup=None):
        if self._pending.get('message_id'):
            await self._bot.edit_message_text(chat_id=self._pending['chat_id'],
                                              message_id=self._pending['message_id'],
                                              text=text, reply_markup=reply_markup)

    async def edit_message_caption(self, caption, reply_markup=None):
        if self._pending.get('message_id'):
            await self._bot.edit_message_caption(chat_id=self._pending['chat_id'],
                                                 message_id=self._pending['message_id'],
                                                 caption=caption, reply_markup=reply_markup)


class _AwaitingReason(filters.MessageFilter):
    """فقط پیامِ ادمینی که «نوشتن دلیل» را زده و هنوز دلیلی نفرستاده."""

    def filter(self, message):
        user = getattr(message, 'from_user', None)
        pending = _PENDING_REASON.get(getattr(user, 'id', None))
        if pending and time.time() - pending['at'] > REASON_TTL:
            _PENDING_REASON.pop(user.id, None)
            return False
        return bool(pending)


async def identity_reason_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """متنِ ادمین بعد از «نوشتن دلیل»: رد با همین دلیل، و خبرش به خریدار."""
    message = update.effective_message
    admin = update.effective_user
    pending = _PENDING_REASON.pop(admin.id, None)
    if not pending or not is_admin(admin.id):
        return
    reason = ' '.join((message.text or '').split())
    if reason in ('انصراف', 'لغو', 'cancel'):
        await message.reply_text('رد لغو شد؛ مدارک هنوز منتظر بررسی است.')
        raise ApplicationHandlerStop
    if len(reason) < 3:
        _PENDING_REASON[admin.id] = pending
        await message.reply_text('دلیل خیلی کوتاه است؛ یک جملهٔ روشن بنویس (یا: انصراف).')
        raise ApplicationHandlerStop
    query = _TypedReason(ctx.bot, message, pending)
    name = admin_display_name(admin)
    if pending['prefix'] == 'site_idv':
        await _site_review(query, ctx, pending['id'], False, reason[:255], name)
    else:
        await _bot_review(query, ctx, pending['id'], False, reason[:255], name)
    raise ApplicationHandlerStop


def identity_reason_handler():
    """در گروهِ ‎-2‎: پیش از قفلِ عضویت و بقیهٔ گفتگوها، فقط برای همان ادمین."""
    return MessageHandler(filters.TEXT & ~filters.COMMAND & _AwaitingReason(), identity_reason_text)


async def _bot_review(query, ctx, verification_id, approve, reason, name):
    decided = await asyncio.to_thread(review_identity_verification, verification_id,
                                      approve=approve, reviewer=name, reason=reason)
    if not decided:
        row = await asyncio.to_thread(get_identity_verification, verification_id)
        who = (row[10] if row else '') or 'ادمین دیگری'
        await query.answer(f'قبلاً بررسی شده ({who})', show_alert=True)
        return
    await query.answer('انجام شد')
    tg, number = decided
    result = (f'🪪 احراز هویت #{verification_id} ({_masked(number)})'
              + ('' if approve else f'\nدلیل رد: {reason}'))
    await broadcast_reviewed(ctx.bot, 'bot_idv', verification_id, approved=approve,
                             reviewer_name=name, result_text=result, extra_admins=[])
    await _edit_decided(query, f'{result}\n\n{review_footer(approved=approve, reviewer_name=name)}')
    try:
        if approve:
            released = await release_and_deliver(ctx.bot, tg)
            ask = await asyncio.to_thread(identity_what_to_ask, tg)
            level = await asyncio.to_thread(identity_level, tg)
            if ask['video']:
                # سفارشی هنوز ویدیو می‌خواهد؛ بدون حرفی از مبلغ، فقط قدمِ بعد.
                await ctx.bot.send_message(chat_id=int(tg), text=(
                    '✅ مدارک احراز هویتت تایید شد.'
                    + (f'\n{fa(len(released))} سفارش منتظرت در حال تحویل است.' if released else '')
                    + f'\nبرای سفارش‌های دیگرت یک مرحلهٔ دیگر مانده: {needs_sentence(ask)}.'),
                    reply_markup=start_keyboard())
            else:
                await ctx.bot.send_message(chat_id=int(tg), text=(
                    '✅ احراز هویتت در اتومیک شاپ تایید شد.'
                    + (f'\n{fa(len(released))} سفارش منتظرت در حال تحویل است.' if released else '')
                    + ('\nاز این به بعد خریدهایت بدون انتظار تحویل می‌شوند.'
                       if level >= LEVEL_VIDEO else '')), reply_markup=main_menu())
        else:
            await ctx.bot.send_message(chat_id=int(tg), text=(
                f'❌ احراز هویتت تایید نشد.\nدلیل: {reason}\n'
                'مدارک را با عکس واضح و طبق نمونه‌ها دوباره بفرست. سفارش‌های منتظرت محفوظ‌اند.'),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    '📤 ارسال دوبارهٔ مدارک', callback_data='idv_resend')]]))
    except Exception as exc:  # noqa: BLE001 — کاربر ربات را بسته
        log.warning('identity result not delivered tg=%s: %s', tg, exc)


async def _site_review(query, ctx, verification_id, approve, reason, name):
    from site_api import call_site_identity_review

    result = await asyncio.to_thread(
        call_site_identity_review, verification_id, 'approve' if approve else 'reject',
        query.from_user.id, name, reason)
    if not (result.get('ok') or result.get('already')):
        err = result.get('message') or result.get('error') or 'خطای سایت'
        await query.answer(f'انجام نشد: {err}', show_alert=True)
        return
    if result.get('already'):
        approve = result.get('status') == 'approved'
        name = result.get('reviewed_by') or name
        await query.answer(f'قبلاً بررسی شده ({name})', show_alert=True)
    else:
        await query.answer('انجام شد')
    released = int(result.get('released') or 0)
    text = (f'🪪 احراز هویت سایت #{verification_id} {result.get("card") or ""}'.strip()
            + (f'\n{released} سفارش منتظر آزاد شد.' if released else '')
            + ('' if approve or not reason else f'\nدلیل رد: {reason}'))
    await broadcast_reviewed(ctx.bot, 'site_idv', verification_id, approved=approve,
                             reviewer_name=name, result_text=text, extra_admins=[])
    await _edit_decided(query, f'{text}\n\n{review_footer(approved=approve, reviewer_name=name)}')


# ── پنل ادمین ───────────────────────────────────────────────────────


async def identity_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """idva_home | idva_open_<id> | idva_order_<id> | idva_rel_<id> | idva_relok_<id>"""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    data = str(query.data or '')
    await query.answer()
    if data == 'idva_home':
        pending, held_count = await asyncio.to_thread(count_identity_queue)
        rows = await asyncio.to_thread(list_pending_identity_verifications, 10)
        held = await asyncio.to_thread(list_identity_held_orders, None, 10)
        site_pending = site_held = 0
        try:
            from site_api import fetch_site_ping

            ping = await asyncio.to_thread(fetch_site_ping)
            site_pending = int(ping.get('pending_identities') or 0)
            site_held = int(ping.get('kyc_held_orders') or 0)
        except Exception:  # noqa: BLE001
            pass
        text = ('🪪 احراز هویت\n━━━━━━━━━━━━━━━\n'
                f'{rule_text()}\n\n'
                f'مدارکِ ربات منتظر بررسی: {pending}\n'
                f'سفارشِ ربات منتظر احراز: {held_count}\n'
                f'مدارکِ سایت منتظر بررسی: {site_pending} (در پنل سایت یا پیام‌های بالا)\n'
                f'سفارشِ سایت منتظر احراز: {site_held}')
        buttons = [[InlineKeyboardButton(
            f'🪪 #{r[0]} · {_masked(r[2])} · {r[1]}' + (' · 🎥' if int(r[5] or 1) >= LEVEL_VIDEO else ''),
            callback_data=f'idva_open_{r[0]}')] for r in rows]
        buttons += [[InlineKeyboardButton(
            f'⏸ سفارش #{o[0]} · {max(0, int(o[2] or 0) - int(o[3] or 0)):,} ت'
            + (' · 🎥' if int(o[6] or 1) >= LEVEL_VIDEO else ''),
            callback_data=f'idva_order_{o[0]}')] for o in held]
        buttons.append([InlineKeyboardButton('🔄 بروزرسانی', callback_data='idva_home'),
                        InlineKeyboardButton('🚨 مرکز عملیات', callback_data='admx_ops')])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return
    parts = data.split('_')
    kind = parts[1] if len(parts) > 1 else ''
    raw = parts[2] if len(parts) > 2 else ''
    code = parts[3] if len(parts) > 3 else ''
    if not raw.isdigit():
        return
    pk = int(raw)
    back = InlineKeyboardMarkup([[InlineKeyboardButton('↩️ احراز هویت', callback_data='idva_home')]])
    if kind == 'open':
        row = await asyncio.to_thread(get_identity_verification, pk)
        if not row or row[7] != 'pending':
            await query.edit_message_text('این مدارک دیگر در صف نیست.', reply_markup=back)
            return
        held = await asyncio.to_thread(list_identity_held_orders, row[1], 20)
        other = await asyncio.to_thread(identity_card_owned_by_other, row[1], row[2])

        class _Who:  # فقط برای متن؛ نام تلگرامی این‌جا در دسترس نیست
            id = row[1]
            username = None
            full_name = ''

        caption = admin_caption(pk, _Who, row[2], row[3], held, other, sent_at=row[12],
                                legacy_selfie=bool(row[6]), level=int(row[13] or 1))
        await send_for_review(ctx.bot, pk, caption, [row[4], row[5], row[6]],
                              recipients=[update.effective_user.id], video_file_id=row[15] or '')
    elif kind == 'order':
        order = await asyncio.to_thread(get_order, pk)
        if not order or order[3] != 'kyc_hold':
            await query.edit_message_text('این سفارش دیگر منتظر احراز نیست.', reply_markup=back)
            return
        held_rows = await asyncio.to_thread(list_identity_held_orders, order[6], 50)
        level = next((int(r[6] or 1) for r in held_rows if int(r[0]) == pk), 1)
        keyboard = held_order_admin_keyboard(pk)
        await query.edit_message_text(
            f'⏸ سفارش #{pk} منتظر احراز هویت\nمبلغ: {int(order[2] or 0):,} تومان · '
            f'کاربر: {order[6] or "—"}\nمدرک لازم: {LEVEL_LABELS.get(level, "عکس مدارک")}\n\n'
            'آزادسازی: اگر خریدار را می‌شناسی. رد + برگشت پول: سفارش لغو و پول به کیف پولش '
            'برمی‌گردد. رد بدون بازگشت وجه: برای تقلب یا کارتِ مالِ کس دیگر؛ پول برنمی‌گردد.',
            reply_markup=InlineKeyboardMarkup(list(keyboard.inline_keyboard) + [
                [InlineKeyboardButton('↩️ احراز هویت', callback_data='idva_home')],
            ]))
    elif kind == 'rel':
        await query.edit_message_text(
            f'سفارش #{pk} بدون احراز هویت تحویل شود؟ این کار ثبت می‌شود.',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ بله، تحویل بده', callback_data=f'idva_relok_{pk}')],
                [InlineKeyboardButton('↩️ نه', callback_data=f'idva_order_{pk}')],
            ]))
    elif kind in ('ref', 'keep'):
        refund = kind == 'ref'
        await query.edit_message_text(
            (f'سفارش #{pk} رد شود و پولش به کیف پول خریدار برگردد؟' if refund else
             f'⛔ سفارش #{pk} رد شود و پولش برنگردد؟ (فقط برای تقلب یا کارتِ مالِ کس دیگر)')
            + '\n\nدلیل را انتخاب کن؛ همین برای خریدار فرستاده می‌شود:',
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(label, callback_data=f'idva_{"rr" if refund else "rk"}_{pk}_{c}')]
                 for c, label in HOLD_REJECT_REASONS.items()]
                + [[InlineKeyboardButton('↩️ نه', callback_data=f'idva_order_{pk}')]]))
    elif kind in ('rr', 'rk'):
        refund = kind == 'rr'
        reason = HOLD_REJECT_REASONS.get(code, HOLD_REJECT_REASONS['other'])
        order = await asyncio.to_thread(get_order, pk)
        name = admin_display_name(update.effective_user)
        ok, refunded, error = await asyncio.to_thread(
            reject_identity_hold_order, pk, refund=refund, reason=reason, reviewer=name)
        if not ok:
            await query.edit_message_text(f'انجام نشد: {error}', reply_markup=back)
            return
        try:
            from db import log_admin_action

            await asyncio.to_thread(log_admin_action, update.effective_user.id,
                                    'identity_hold_rejected', 'order', pk,
                                    f'refund={refund} amount={refunded} reason={reason}')
        except Exception:  # noqa: BLE001
            log.exception('admin action log failed order=%s', pk)
        await query.edit_message_text(
            (f'↩️ سفارش #{pk} رد شد و {refunded:,} تومان به کیف پول خریدار برگشت.' if refund else
             f'⛔ سفارش #{pk} بدون بازگشت وجه رد شد.') + f'\nدلیل: {reason}', reply_markup=back)
        tg = order[6] if order else None
        if tg:
            text = (f'❌ سفارش #{pk} پس از بررسی احراز هویت رد شد.\nدلیل: {reason}\n'
                    + (f'{fa_money(refunded)} تومان به کیف پولت برگشت.' if refund and refunded else
                       'برای پیگیری با پشتیبانی تماس بگیر.'))
            try:
                await ctx.bot.send_message(chat_id=int(tg), text=text)
            except Exception as exc:  # noqa: BLE001 — خریدار ربات را بسته
                log.warning('hold rejection notice not delivered tg=%s: %s', tg, exc)
    elif kind == 'relok':
        order = await asyncio.to_thread(get_order, pk)
        if not await asyncio.to_thread(release_identity_hold_order, pk):
            await query.edit_message_text('این سفارش دیگر منتظر احراز نیست.', reply_markup=back)
            return
        log.warning('order %s released WITHOUT identity verification by tg=%s', pk,
                    update.effective_user.id)
        try:
            from db import log_admin_action

            await asyncio.to_thread(log_admin_action, update.effective_user.id,
                                    'identity_hold_released', 'order', pk, 'without verification')
        except Exception:  # noqa: BLE001
            log.exception('admin action log failed order=%s', pk)
        await query.edit_message_text(f'▶️ سفارش #{pk} آزاد شد و تحویلش شروع شد.', reply_markup=back)
        await release_and_deliver(ctx.bot, order[6] if order else None, order_ids=[pk])


# ── سفارشِ منتظرِ احرازِ سایت (دکمه‌های پیامِ صفِ سایت) ─────────────────

_SITE_ACTIONS = {'rel': 'release', 'ref': 'refund', 'keep': 'keep'}


async def site_held_order(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """skyc_<rel|ref|keep>_<id> → تایید/دلیل؛ skyc_do_<rel|ref|keep>_<id>[_<code>] → انجام."""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    parts = str(query.data or '').split('_')
    if len(parts) >= 3 and parts[1] == 'back' and parts[2].isdigit():
        await query.answer()
        await _swap_keyboard(query, held_order_admin_keyboard(int(parts[2]), prefix='skyc'))
        return
    if len(parts) == 3 and parts[1] in _SITE_ACTIONS and parts[2].isdigit():
        await query.answer()
        pk, action = int(parts[2]), parts[1]
        if action == 'rel':
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ بله، بدون احراز آزاد کن', callback_data=f'skyc_do_rel_{pk}')],
                [InlineKeyboardButton('↩️ نه', callback_data=f'skyc_back_{pk}')],
            ])
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(('↩️ ' if action == 'ref' else '⛔ ') + label,
                                       callback_data=f'skyc_do_{action}_{pk}_{c}')]
                 for c, label in HOLD_REJECT_REASONS.items()]
                + [[InlineKeyboardButton('↩️ نه', callback_data=f'skyc_back_{pk}')]])
        await _swap_keyboard(query, keyboard)
        return
    if len(parts) >= 4 and parts[1] == 'do' and parts[2] in _SITE_ACTIONS and parts[3].isdigit():
        from site_api import call_site_held_order_action

        pk, action = int(parts[3]), _SITE_ACTIONS[parts[2]]
        code = parts[4] if len(parts) > 4 else ''
        reason = HOLD_REJECT_REASONS.get(code, '') if action != 'release' else ''
        name = admin_display_name(update.effective_user)
        result = await asyncio.to_thread(call_site_held_order_action, pk, action,
                                         update.effective_user.id, name, reason)
        if not result.get('ok'):
            err = result.get('message') or result.get('error') or 'خطای سایت'
            await query.answer(f'انجام نشد: {err}', show_alert=True)
            if result.get('already'):
                await _swap_keyboard(query, None)
            return
        await query.answer('انجام شد')
        label = {'release': '▶️ آزاد شد و تحویلش شروع شد',
                 'refund': '↩️ رد شد و پول به کیف پول برگشت',
                 'keep': '⛔ بدون بازگشت وجه رد شد'}[action]
        await _append_result(query, f'{label} — {name}' + (f'\nدلیل: {reason}' if reason else ''))
        return
    await query.answer('دادهٔ نامعتبر', show_alert=True)


async def _swap_keyboard(query, keyboard):
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except BadRequest as exc:
        if 'not modified' not in str(exc).lower():
            raise


async def _append_result(query, line):
    """نتیجه زیر همان پیام می‌نشیند و دکمه‌ها برداشته می‌شوند."""
    message = query.message
    try:
        if message and (message.photo or message.document):
            await query.edit_message_caption(
                caption=f'{(message.caption or "")[:900]}\n\n{line}', reply_markup=None)
        else:
            await query.edit_message_text(text=f'{(message.text or "")[:3500]}\n\n{line}',
                                          reply_markup=None)
    except BadRequest as exc:
        if 'not modified' not in str(exc).lower():
            log.warning('could not show the held-order result: %s', exc)


def identity_admin_handlers():
    return [
        CallbackQueryHandler(identity_review, pattern=r'^(?:site_idv|idv)_(?:ok|no|rj|back|why)_\d+'),
        CallbackQueryHandler(identity_admin, pattern=r'^idva_'),
        CallbackQueryHandler(site_held_order, pattern=r'^skyc_'),
        CallbackQueryHandler(terms_accept, pattern=r'^terms_ok_\d+$'),
    ]
