"""احراز کارت بانکی پیش از کارت‌به‌کارت — یک‌بار برای همیشه.

مراحل: شمارهٔ موبایلِ خودِ حساب تلگرام (دکمهٔ «اشتراک شماره»؛ تلگرام فقط
شمارهٔ واقعیِ همان حساب را می‌فرستد) → شماره کارت → نام صاحب کارت → عکس
کارت. ادمین یک‌بار تایید می‌کند و از آن به بعد کارت‌به‌کارت باز است.
"""
from __future__ import annotations

import asyncio
import logging
import re

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove, Update,
)
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler,
    filters,
)

from admin_notify import admin_ids, is_admin
from db import (
    approved_cards, card_kyc_state, card_owned_by_other, get_card_verification,
    review_card_verification, submit_card_verification,
)
from keyboards import main_menu
from review_broadcast import admin_display_name, broadcast_reviewed, remember_notices, review_footer

log = logging.getLogger(__name__)

WAIT_PHONE, WAIT_NUMBER, WAIT_NAME, WAIT_PHOTO = range(4)

_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
# فقط callbackهایی که بعد از تایید برای ادامهٔ پرداخت برمی‌گردیم.
_RESUME_RE = re.compile(r'^(?:pay_card_\d+|wpay_card_\d+)$')


def normalize_card(raw) -> str:
    return re.sub(r'\D', '', str(raw or '').translate(_DIGITS))


def luhn_ok(number: str) -> bool:
    if not number.isdigit() or len(number) != 16:
        return False
    total = 0
    for index, ch in enumerate(reversed(number)):
        digit = int(ch)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def normalize_phone(raw) -> str:
    phone = re.sub(r'\D', '', str(raw or '').translate(_DIGITS))
    if phone.startswith('98') and len(phone) == 12:
        phone = '0' + phone[2:]
    return phone


def pretty_card(number: str) -> str:
    return '-'.join(number[i:i + 4] for i in range(0, len(number), 4))


def masked_card(number: str) -> str:
    return f'{number[:4]}-{number[4:6]}**-****-{number[12:]}' if len(number) == 16 else number


def _cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton('❌ انصراف', callback_data='cverify_cancel')]])


def gate_keyboard(resume_callback: str, state: str):
    rows = []
    if state != 'pending':
        rows.append([InlineKeyboardButton('💳 احراز کارت بانکی',
                                          callback_data=f'cverify_go_{resume_callback}')])
    else:
        rows.append([InlineKeyboardButton('🔄 بررسی دوباره', callback_data=resume_callback)])
    rows.append([InlineKeyboardButton('🏠 منوی اصلی', callback_data='home')])
    return InlineKeyboardMarkup(rows)


def gate_text(state: str) -> str:
    if state == 'pending':
        return ('⏳ *کارتت در صف بررسی ادمین است.*\n'
                'معمولاً چند دقیقه طول می‌کشد. بعد از تایید خبرت می‌کنیم.\n\n'
                '⚠️ تا تایید نشده *واریز نکن*.')
    extra = '\n\n❌ احراز قبلی رد شد؛ با عکس واضح دوباره بفرست.' if state == 'rejected' else ''
    return ('🔐 *احراز کارت بانکی*\n'
            '━━━━━━━━━━━━━━━\n'
            'برای امنیت پرداخت‌ها، کارت‌به‌کارت فقط از کارتی پذیرفته می‌شود که *یک‌بار* '
            'احراز شده باشد. بعد از تایید، دفعه‌های بعد لازم نیست.\n\n'
            'لازم است: شماره موبایلِ همین حساب تلگرام، شماره و نامِ روی کارت، و عکس کارت.'
            + extra)


async def card_gate(query, telegram_id, resume_callback) -> bool:
    """True یعنی آزاد است. در غیر این صورت پیام احراز را نشان می‌دهد."""
    approved = await asyncio.to_thread(approved_cards, telegram_id)
    if approved:
        return True
    state = await asyncio.to_thread(card_kyc_state, telegram_id)
    await query.edit_message_text(gate_text(state), parse_mode='Markdown',
                                  reply_markup=gate_keyboard(resume_callback, state))
    return False


async def payer_cards_line(telegram_id) -> str:
    """یک خط برای کپشن رسیدِ ادمین: از کدام کارتِ تاییدشده باید واریز آمده باشد."""
    try:
        cards = await asyncio.to_thread(approved_cards, telegram_id)
    except Exception:  # noqa: BLE001
        return ''
    if not cards:
        return '⚠️ کارت احرازشده‌ای ثبت نشده — بدون تطبیق در بانک تایید نکن.'
    parts = [f'{pretty_card(c[1])} ({c[2]})' for c in cards[:3]]
    return '💳 کارت‌های احرازشده: ' + ' | '.join(parts) + '\nواریز باید از یکی از همین‌ها باشد.'


# ── گفتگو ────────────────────────────────────────────────────────────


async def cverify_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    resume = query.data.replace('cverify_go_', '', 1)
    ctx.user_data['cverify'] = {'resume': resume if _RESUME_RE.match(resume) else ''}
    await query.edit_message_text(
        '📱 *قدم ۱ از ۴ — شماره موبایل*\n'
        'دکمهٔ «اشتراک شماره» پایین صفحه را بزن. فقط شمارهٔ همین حساب تلگرام پذیرفته می‌شود.',
        parse_mode='Markdown', reply_markup=_cancel_kb())
    await query.message.reply_text(
        'برای ادامه دکمهٔ زیر را بزن 👇',
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton('📱 اشتراک شماره موبایل', request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True))
    return WAIT_PHONE


async def cverify_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact is None or contact.user_id != update.effective_user.id:
        await update.message.reply_text(
            '❌ فقط شمارهٔ *خودِ همین حساب* پذیرفته می‌شود؛ دکمهٔ «اشتراک شماره» را بزن.',
            parse_mode='Markdown')
        return WAIT_PHONE
    phone = normalize_phone(contact.phone_number)
    if not (phone.startswith('09') and len(phone) == 11):
        await update.message.reply_text(
            '❌ فقط شمارهٔ موبایل ایران (۰۹…) پذیرفته می‌شود.', reply_markup=ReplyKeyboardRemove())
        ctx.user_data.pop('cverify', None)
        return ConversationHandler.END
    ctx.user_data.setdefault('cverify', {})['phone'] = phone
    await update.message.reply_text(
        f'✅ شماره {phone} ثبت شد.\n\n💳 *قدم ۲ از ۴ — شماره کارت*\n'
        'شمارهٔ ۱۶ رقمیِ کارتی که با آن واریز می‌کنی را بفرست.',
        parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())
    return WAIT_NUMBER


async def cverify_number(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    number = normalize_card(update.message.text)
    if len(number) != 16 or not luhn_ok(number):
        await update.message.reply_text('❌ شماره کارت معتبر نیست؛ ۱۶ رقم را با دقت بفرست.',
                                        reply_markup=_cancel_kb())
        return WAIT_NUMBER
    if await asyncio.to_thread(card_owned_by_other, update.effective_user.id, number):
        log.warning('card %s…%s offered by tg=%s is on another account',
                    number[:6], number[-4:], update.effective_user.id)
        await update.message.reply_text(
            '❌ این کارت روی حساب دیگری ثبت شده است. اگر کارت مال خودت است به پشتیبانی پیام بده.',
            reply_markup=main_menu())
        ctx.user_data.pop('cverify', None)
        return ConversationHandler.END
    ctx.user_data.setdefault('cverify', {})['number'] = number
    await update.message.reply_text(
        '👤 *قدم ۳ از ۴ — نام صاحب کارت*\nنام و نام خانوادگی را همان‌طور که روی کارت است بنویس.',
        parse_mode='Markdown', reply_markup=_cancel_kb())
    return WAIT_NAME


async def cverify_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = ' '.join(str(update.message.text or '').split())[:120]
    if len(name) < 4 or any(ch.isdigit() for ch in name.translate(_DIGITS)):
        await update.message.reply_text('❌ نام کامل صاحب کارت را بنویس (بدون عدد).',
                                        reply_markup=_cancel_kb())
        return WAIT_NAME
    ctx.user_data.setdefault('cverify', {})['name'] = name
    await update.message.reply_text(
        '📷 *قدم ۴ از ۴ — عکس کارت*\n'
        'از روی کارت عکس بگیر؛ شماره و نام خوانا باشد. تاریخ انقضا و CVV2 را می‌توانی بپوشانی — '
        'آن‌ها را لازم نداریم و هرگز نفرست.',
        parse_mode='Markdown', reply_markup=_cancel_kb())
    return WAIT_PHOTO


async def cverify_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = ctx.user_data.get('cverify') or {}
    if not update.message.photo or not all(data.get(k) for k in ('phone', 'number', 'name')):
        await update.message.reply_text('لطفاً *عکس* کارت را بفرست.', parse_mode='Markdown')
        return WAIT_PHOTO
    user = update.effective_user
    file_id = update.message.photo[-1].file_id
    card_id, status = await asyncio.to_thread(
        submit_card_verification, user.id, data['number'], data['name'], data['phone'],
        file_id, data.get('resume', ''))
    ctx.user_data.pop('cverify', None)
    if status == 'approved':
        await update.message.reply_text('✅ این کارت قبلاً تایید شده است.',
                                        reply_markup=_resume_kb(data.get('resume', '')))
        return ConversationHandler.END
    uname = f'@{user.username}' if user.username else '—'
    caption = (
        '💳 احراز کارت بانکی — ربات\n'
        '━━━━━━━━━━━━━━━\n'
        f'شماره کارت: {pretty_card(data["number"])}\n'
        f'نام روی کارت: {data["name"]}\n'
        f'📞 موبایلِ حساب تلگرام: {data["phone"]}\n'
        f'کاربر: {user.full_name} ({uname}) · {user.id}\n\n'
        'نام روی کارت و عکس را تطبیق بده.'
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ تایید کارت', callback_data=f'cverify_ok_{card_id}'),
        InlineKeyboardButton('❌ رد کارت', callback_data=f'cverify_no_{card_id}'),
    ]])
    notices = []
    for aid in admin_ids():
        try:
            msg = await ctx.bot.send_photo(chat_id=aid, photo=file_id, caption=caption,
                                           reply_markup=keyboard)
            notices.append({'chat_id': int(aid), 'message_id': msg.message_id})
        except Exception as exc:  # noqa: BLE001
            log.warning('card verification notice failed admin=%s: %s', aid, exc)
    remember_notices('bot_card', card_id, notices)
    await update.message.reply_text(
        '✅ مدارک ثبت شد و در صف بررسی است. بعد از تایید همین‌جا خبرت می‌کنیم.\n'
        '⚠️ تا تایید نشده واریز نکن.', reply_markup=main_menu())
    return ConversationHandler.END


async def cverify_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('cverify', None)
    if update.callback_query:
        await update.callback_query.answer('لغو شد')
        await update.callback_query.edit_message_text('احراز کارت لغو شد.')
        await update.callback_query.message.reply_text('منوی اصلی:', reply_markup=main_menu())
    else:
        await update.message.reply_text('احراز کارت لغو شد.', reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text('منوی اصلی:', reply_markup=main_menu())
    return ConversationHandler.END


def _resume_kb(resume):
    rows = []
    if resume and _RESUME_RE.match(resume):
        rows.append([InlineKeyboardButton('💳 ادامهٔ پرداخت کارت‌به‌کارت', callback_data=resume)])
    rows.append([InlineKeyboardButton('🏠 منوی اصلی', callback_data='home')])
    return InlineKeyboardMarkup(rows)


# ── تصمیم ادمین ──────────────────────────────────────────────────────


async def cverify_review(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer('دسترسی نداری', show_alert=True)
        return
    approve = query.data.startswith('cverify_ok_')
    try:
        card_id = int(query.data.rsplit('_', 1)[-1])
    except ValueError:
        await query.answer('دادهٔ نامعتبر', show_alert=True)
        return
    name = admin_display_name(update.effective_user)
    decided = await asyncio.to_thread(
        review_card_verification, card_id, approve=approve, reviewer=name,
        reason='' if approve else 'مدارک نامعتبر یا ناخوانا')
    row = await asyncio.to_thread(get_card_verification, card_id)
    if not decided:
        who = (row[8] if row else '') or 'ادمین دیگری'
        await query.answer(f'قبلاً بررسی شده ({who})', show_alert=True)
        return
    await query.answer('انجام شد')
    footer = review_footer(approved=approve, reviewer_name=name)
    await broadcast_reviewed(ctx.bot, 'bot_card', card_id, approved=approve,
                             reviewer_name=name, result_text='', extra_admins=[])
    try:
        await query.edit_message_caption(caption=footer, reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    if not row:
        return
    tg, number, resume = row[1], row[2], row[7]
    try:
        if approve:
            await ctx.bot.send_message(
                chat_id=int(tg),
                text=(f'✅ کارت {masked_card(number)} تایید شد.\n'
                      'حالا می‌توانی با همین کارت کارت‌به‌کارت کنی.'),
                reply_markup=_resume_kb(resume))
        else:
            await ctx.bot.send_message(
                chat_id=int(tg),
                text=(f'❌ کارت {masked_card(number)} تایید نشد.\n'
                      'عکس واضح‌تری بفرست و نام را دقیقاً مثل روی کارت بنویس.'),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    '💳 احراز دوباره', callback_data=f'cverify_go_{resume or "none"}')]]))
    except Exception as exc:  # noqa: BLE001 — کاربر ربات را بسته
        log.warning('card verification result not delivered tg=%s: %s', tg, exc)


def card_verify_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cverify_start, pattern=r'^cverify_go_[a-z_0-9]+$')],
        states={
            WAIT_PHONE: [MessageHandler(filters.CONTACT, cverify_phone),
                         MessageHandler(filters.TEXT & ~filters.COMMAND, cverify_phone)],
            WAIT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, cverify_number)],
            WAIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, cverify_name)],
            WAIT_PHOTO: [MessageHandler(filters.PHOTO, cverify_photo),
                         MessageHandler(filters.ALL & ~filters.COMMAND, cverify_photo)],
        },
        fallbacks=[
            CommandHandler('cancel', cverify_cancel),
            CallbackQueryHandler(cverify_cancel, pattern=r'^cverify_cancel$'),
        ],
        allow_reentry=True,
    )
