"""پنل مدیریت ادمین — فقط برای ADMIN_CHAT_ID."""
import asyncio
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, CommandHandler, filters,
)

from admin_notify import is_admin, is_credential_admin, notify_admin
from keyboards import (
    admin_home_keyboard, admin_user_keyboard, admin_failed_order_keyboard,
    admin_stuck_order_keyboard, admin_ticket_keyboard, main_menu,
    credential_admin_home_keyboard,
)
from db import (
    get_admin_stats, list_recent_users, list_users_with_balance, get_user_profile,
    find_user_by_username, set_user_blocked, list_failed_deliveries,
    list_open_orders, admin_adjust_wallet, admin_set_wallet_balance,
    list_wallet_txs, get_user_orders, get_order, list_open_tickets, get_ticket,
    close_ticket, add_ticket_message, admin_operations_snapshot, get_setting,
    log_admin_action, admin_mark_order_delivered, admin_cancel_stuck_order,
    mark_delivery_notified, order_refund_amount, count_ready_credential_orders,
    count_open_tickets,
)
from handlers.payment import fulfill_order_async

WAIT_FIND = 1
WAIT_MSG = 2
WAIT_WALLET = 3
WAIT_TICKET_REPLY = 4
WAIT_WALLET_SET = 5


def _deny_text():
    return "❌ این دستور برای شما فعال نیست."


def _edit_safe(query, text, reply_markup=None, parse_mode='Markdown'):
    """ویرایش امن پیام؛ خطای «Message is not modified» را بی‌صدا رد می‌کند.

    دکمه‌های رفرش/بروزرسانی پنل ادمین اغلب همان متن قبلی را دوباره edit می‌کنند؛
    تلگرام این‌جا ۴۰۰ برمی‌گرداند که نباید به اعلان «خطای داکر» منجر شود.
    """
    try:
        return query.edit_message_text(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    except Exception as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


async def _require_admin(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if is_admin(uid):
        return True
    if update.callback_query:
        await update.callback_query.answer(_deny_text(), show_alert=True)
        try:
            await update.callback_query.edit_message_text(_deny_text())
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text(_deny_text())
    return False


def _ticket_category(ticket):
    if not ticket or len(ticket) < 9:
        return 'other'
    return str(ticket[8] or 'other')


async def _can_manage_ticket(update: Update, ticket) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if not ticket:
        if update.callback_query:
            await update.callback_query.answer('تیکت پیدا نشد.', show_alert=True)
        return False
    if is_admin(uid):
        return True
    if is_credential_admin(uid) and _ticket_category(ticket) == 'credential':
        return True
    if update.callback_query:
        await update.callback_query.answer(_deny_text(), show_alert=True)
    elif update.message:
        await update.message.reply_text(_deny_text())
    return False


def _tg_handle(uname):
    un = (uname or '').lstrip('@').strip()
    if not un or un.startswith('tg_'):
        return '—'
    return f'@{un}'


def _format_user(p):
    _db_id, tg, uname, first, last, blocked, reason, bal, joined = p
    name = f"{first or ''} {last or ''}".strip() or "—"
    handle = _tg_handle(uname)
    st = f"🚫 بلاک — {reason or '—'}" if blocked else "✅ فعال"
    joined_s = str(joined)[:16] if joined else "—"
    return (
        f"✦ *کارت کاربر*\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"نام: {name}\n"
        f"آیدی: *{handle}*\n"
        f"شناسه عددی: `{tg}`\n"
        f"وضعیت: {st}\n"
        f"کیف پول: *{bal:,}* تومان\n"
        f"عضویت: {joined_s}"
    )


async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    if is_credential_admin(uid) and not is_admin(uid):
        await update.message.reply_text(
            "🔐 دسترسی شما فقط بخش جم با اطلاعات است.\n"
            "دستور /credadmin را بزن."
        )
        return
    if not await _require_admin(update):
        return
    await _show_home(update, ctx, via_message=True)


async def admin_home_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    await _show_home(update, ctx, via_message=False)


async def _show_home(update, ctx, via_message=False):
    s = get_admin_stats()
    try:
        threshold = max(
            0, min(int(get_setting('low_stock_threshold', '5') or 5), 10_000)
        )
    except (TypeError, ValueError):
        threshold = 5
    ops = admin_operations_snapshot(threshold)
    ready_creds = count_ready_credential_orders()
    alerts = (
        ops['pending_receipts'] + ops['stuck_processing']
        + ops['failed_payments_24h'] + ops['open_tickets']
        + ops['low_gem_stock'] + ops['low_store_stock']
        + ready_creds
    )
    orders_action = (
        ops['pending_receipts'] + ops['stuck_processing']
        + ready_creds + int(s.get('failed_g2') or 0)
    )
    text = (
        f"✦ *پنل ادمین Atomic*\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"👥 کاربران: *{s['users']:,}*  ·  🚫 بلاک: {s['blocked']}\n"
        f"📦 سفارش‌ها: *{s['orders']:,}*  ·  باز: {s['open_orders']}\n"
        f"❌ تحویل ناموفق: *{s['failed_g2']:,}*\n"
        f"🔐 جم با اطلاعات (آماده): *{ready_creds:,}*\n"
        f"💰 برگشت کیف‌پول (۷روز): *{ops.get('wallet_refunds_7d', 0):,}*\n"
        f"🎫 تیکت باز: *{s['open_tickets']:,}*\n"
        f"🏦 مجموع کیف پول‌ها: *{s['wallet_sum']:,}* ت\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📅 فروش امروز: *{ops['sales_today_amount']:,}* ت "
        f"({ops['sales_today_count']:,} سفارش)\n"
        f"{'🚨 نیاز به اقدام:' if alerts else '✅ وضعیت عادی — هشدار:'} *{alerts:,}*"
    )
    kb = admin_home_keyboard({
        'ops_alerts': alerts,
        'orders_action': orders_action,
        'open_tickets': s['open_tickets'],
    })
    if via_message:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)
    else:
        await _edit_safe(update.callback_query, text, reply_markup=kb)


async def admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    try:
        rows = list_recent_users(15)
    except Exception as e:
        await query.edit_message_text(
            f"❌ خطا در دریافت کاربران:\n{e}",
            reply_markup=admin_home_keyboard(),
        )
        return
    if not rows:
        await query.edit_message_text(
            "هنوز کاربری ثبت نشده.",
            reply_markup=admin_home_keyboard(),
        )
        return
    lines = ["✦ آخرین کاربران", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    for r in rows:
        _db_id, tg, name, uname, blocked, bal = r
        handle = _tg_handle(uname)
        safe_name = (name or '—').replace('\n', ' ')[:24]
        flag = "🚫" if blocked else "·"
        lines.append(
            f"{flag} {handle}  ·  {safe_name}\n"
            f"   {tg}  ·  {int(bal or 0):,} ت"
        )
        label = f"{'🚫 ' if blocked else ''}{handle if handle != '—' else (safe_name or str(tg))}"
        buttons.append([InlineKeyboardButton(label[:40], callback_data=f'adm_user_{tg}')])
        buttons.append([
            InlineKeyboardButton(f'➕ شارژ {tg}', callback_data=f'adm_wal_{tg}'),
            InlineKeyboardButton(f'➖ کسر {tg}', callback_data=f'adm_wdeduct_{tg}'),
        ])
    lines.append("\nجستجو با آیدی @user یا شناسه عددی")
    buttons.append([InlineKeyboardButton('🔎 جستجو', callback_data='adm_find')])
    buttons.append([InlineKeyboardButton('🔙 کاربران', callback_data='admx_hub_users')])
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_users_with_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """کاربرانی که موجودی کیف پول دارند — با دکمه شارژ/کسر مستقیم."""
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    try:
        rows = await asyncio.to_thread(list_users_with_balance, 30)
    except Exception as e:
        await query.edit_message_text(
            f"❌ خطا در دریافت کاربران:\n{e}",
            reply_markup=admin_home_keyboard(),
        )
        return
    lines = ["💰 کاربران دارای موجودی", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    if not rows:
        lines.append("کاربری با موجودی نیست.")
    else:
        for r in rows:
            _db_id, tg, name, uname, blocked, bal = r
            tg = str(tg or '').strip()
            if not tg:
                continue
            handle = _tg_handle(uname)
            safe_name = (name or '—').replace('\n', ' ')[:24]
            flag = "🚫" if blocked else "·"
            try:
                amount = int(bal or 0)
            except (TypeError, ValueError):
                amount = 0
            lines.append(
                f"{flag} {handle}  ·  {safe_name}\n"
                f"   {tg}  ·  موجودی {amount:,} ت"
            )
            buttons.append([
                InlineKeyboardButton(f'➖ کسر {tg}', callback_data=f'adm_wdeduct_{tg}'),
                InlineKeyboardButton(f'➕ شارژ {tg}', callback_data=f'adm_wal_{tg}'),
            ])
    buttons.append([InlineKeyboardButton('🔙 کاربران', callback_data='admx_hub_users')])
    try:
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        if 'message is not modified' in str(e).lower():
            return
        await query.message.reply_text(
            f"❌ نمایش لیست ممکن نشد:\n{e}",
            reply_markup=admin_home_keyboard(),
        )


async def admin_user_card(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    tg = query.data.replace('adm_user_', '')
    await _send_user_card(query, tg)


async def admin_user_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    text = (update.message.text or '').strip()
    tg = text.replace('/u_', '').strip()
    if not tg.isdigit():
        await update.message.reply_text("فرمت: `/u_639344728`", parse_mode='Markdown')
        return
    profile = get_user_profile(telegram_id=tg)
    if not profile:
        await update.message.reply_text("کاربر پیدا نشد.")
        return
    await update.message.reply_text(
        _format_user(profile),
        parse_mode='Markdown',
        reply_markup=admin_user_keyboard(tg, profile[5]),
    )


async def _send_user_card(query, tg):
    profile = get_user_profile(telegram_id=tg)
    if not profile:
        await query.edit_message_text("کاربر پیدا نشد.")
        return
    await query.edit_message_text(
        _format_user(profile),
        parse_mode='Markdown',
        reply_markup=admin_user_keyboard(tg, profile[5]),
    )


async def admin_find_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return ConversationHandler.END
    await query.edit_message_text(
        "🔎 جستجوی کاربر\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        "آیدی بفرست: `@username`\n"
        "یا شناسه عددی: `639344728`",
        parse_mode='Markdown',
    )
    return WAIT_FIND


async def admin_find_recv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    raw = (update.message.text or '').strip()
    profile = None
    if raw.startswith('@') or (raw and not raw.isdigit()):
        profile = find_user_by_username(raw)
    elif raw.isdigit():
        profile = get_user_profile(telegram_id=raw)
    else:
        await update.message.reply_text("آیدی `@user` یا عدد بفرست.")
        return WAIT_FIND
    if not profile:
        await update.message.reply_text("کاربر پیدا نشد. (باید حداقل یک‌بار ربات را استارت کرده باشد)")
        return ConversationHandler.END
    tg = profile[1]
    await update.message.reply_text(
        _format_user(profile),
        parse_mode='Markdown',
        reply_markup=admin_user_keyboard(tg, profile[5]),
    )
    return ConversationHandler.END


async def admin_block_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    parts = query.data.split('_')
    flag = parts[2]
    tg = '_'.join(parts[3:])
    blocked = flag == '1'
    set_user_blocked(tg, blocked=blocked, reason='توسط ادمین' if blocked else '')
    try:
        if blocked:
            await ctx.bot.send_message(
                chat_id=int(tg),
                text="🚫 حساب شما در ربات بلاک شد.",
            )
        else:
            await ctx.bot.send_message(
                chat_id=int(tg),
                text="✅ بلاک برداشته شد.",
                reply_markup=main_menu(),
            )
    except Exception:
        pass
    await _send_user_card(query, tg)


async def admin_msg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return ConversationHandler.END
    tg = query.data.replace('adm_msg_', '')
    ctx.user_data['adm_msg_tg'] = tg
    await query.edit_message_text(
        f"✉️ پیام برای `{tg}` را بفرست\n/cancel انصراف",
        parse_mode='Markdown',
    )
    return WAIT_MSG


async def admin_msg_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    tg = ctx.user_data.pop('adm_msg_tg', None)
    if not tg:
        return ConversationHandler.END
    text = update.message.text or ''
    try:
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=f"📨 *پیام پشتیبانی Atomic:*\n\n{text}",
            parse_mode='Markdown',
        )
        await update.message.reply_text(
            "✅ ارسال شد.",
            reply_markup=admin_user_keyboard(tg, False),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال نشد: {e}")
    return ConversationHandler.END


async def admin_wallet_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return ConversationHandler.END
    tg = query.data.replace('adm_wal_', '')
    ctx.user_data['adm_wal_tg'] = tg
    ctx.user_data['adm_wal_mode'] = 'charge'
    await query.edit_message_text(
        f"💰 شارژ کیف پول `{tg}`\n"
        f"عدد مثبت بفرست (مثال: `50000`)\n/cancel",
        parse_mode='Markdown',
    )
    return WAIT_WALLET


async def admin_wallet_deduct_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return ConversationHandler.END
    tg = query.data.replace('adm_wdeduct_', '')
    ctx.user_data['adm_wal_tg'] = tg
    ctx.user_data['adm_wal_mode'] = 'deduct'
    profile = get_user_profile(telegram_id=tg)
    cur = int(profile[7]) if profile else 0
    await query.edit_message_text(
        f"➖ کسر کیف پول `{tg}`\n"
        f"موجودی فعلی: *{cur:,}* ت\n"
        f"عدد مثبت بفرست (مثال: `20000`) تا این مقدار کسر شود\n/cancel",
        parse_mode='Markdown',
    )
    return WAIT_WALLET


async def admin_wallet_apply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    tg = ctx.user_data.pop('adm_wal_tg', None)
    mode = ctx.user_data.pop('adm_wal_mode', 'charge')
    raw = (update.message.text or '').strip().replace(',', '').replace('،', '')
    if not raw.isdigit() or int(raw) <= 0:
        await update.message.reply_text("فقط عدد مثبت بفرست.")
        ctx.user_data['adm_wal_tg'] = tg
        ctx.user_data['adm_wal_mode'] = mode
        return WAIT_WALLET
    amount = int(raw)
    if mode == 'deduct':
        amount = -amount
    profile = get_user_profile(telegram_id=tg)
    if not profile:
        await update.message.reply_text("کاربر پیدا نشد.")
        return ConversationHandler.END
    ok, new_bal, err = admin_adjust_wallet(profile[0], amount, desc=f'ادمین → tg:{tg}')
    if not ok:
        await update.message.reply_text(f"❌ {err}")
        return ConversationHandler.END
    change_label = "شارژ شد" if mode == 'charge' else "کسر شد"
    try:
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=(
                f"💰 کیف پول شما {change_label}.\n"
                f"تغییر: *{abs(amount):,}* ت\n"
                f"موجودی: *{new_bal:,}* ت"
            ),
            parse_mode='Markdown',
        )
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ {change_label}: *{abs(amount):,}* ت → موجودی *{new_bal:,}* ت",
        parse_mode='Markdown',
        reply_markup=admin_user_keyboard(tg, profile[5]),
    )
    return ConversationHandler.END


async def admin_wallet_empty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    confirm = query.data.startswith('adm_wempty_confirm_')
    tg = query.data.replace(
        'adm_wempty_confirm_' if confirm else 'adm_wempty_', ''
    )
    profile = get_user_profile(telegram_id=tg)
    if not profile:
        await query.edit_message_text("کاربر پیدا نشد.")
        return
    if not confirm:
        ctx.user_data['adm_wempty_confirm'] = {
            'tg': tg, 'armed_at': time.time(),
        }
        await query.edit_message_text(
            f"⚠️ موجودی فعلی کاربر `{tg}` برابر *{profile[7]:,} تومان* است.\n"
            "خالی‌کردن کیف پول عملیات مالی حساس است. تأیید می‌کنی؟",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    'بله، موجودی صفر شود',
                    callback_data=f'adm_wempty_confirm_{tg}',
                ),
                InlineKeyboardButton(
                    'انصراف', callback_data=f'adm_user_{tg}'
                ),
            ]]),
        )
        return
    armed = ctx.user_data.pop('adm_wempty_confirm', None) or {}
    if (
        str(armed.get('tg') or '') != str(tg)
        or time.time() - float(armed.get('armed_at') or 0) > 120
    ):
        await query.edit_message_text(
            'تأیید منقضی یا نامعتبر است؛ دوباره از کارت کاربر شروع کن.',
            reply_markup=admin_user_keyboard(tg, profile[5]),
        )
        return
    ok, old, new_bal, err = admin_set_wallet_balance(
        profile[0], 0, desc=f'خالی کردن توسط ادمین tg:{tg}'
    )
    if not ok:
        await query.edit_message_text(
            err or 'خطا',
            reply_markup=admin_user_keyboard(tg, profile[5]),
        )
        return
    log_admin_action(
        update.effective_user.id, 'wallet_emptied', 'user', tg,
        f'old_balance={old}',
    )
    try:
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=f"💰 کیف پول شما توسط پشتیبانی خالی شد.\nموجودی قبلی: {old:,} ت",
        )
    except Exception:
        pass
    await query.edit_message_text(
        f"✅ کیف پول خالی شد.\nقبل: {old:,} ت → الان: {new_bal:,} ت",
        reply_markup=admin_user_keyboard(tg, profile[5]),
    )


async def admin_wallet_set_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return ConversationHandler.END
    tg = query.data.replace('adm_wset_', '')
    ctx.user_data['adm_wset_tg'] = tg
    profile = get_user_profile(telegram_id=tg)
    cur = int(profile[7]) if profile else 0
    await query.edit_message_text(
        f"✏️ تنظیم موجودی دقیق `{tg}`\n"
        f"موجودی فعلی: *{cur:,}* ت\n"
        f"عدد جدید را بفرست (مثلاً `150000`)\n/cancel",
        parse_mode='Markdown',
    )
    return WAIT_WALLET_SET


async def admin_wallet_set_apply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    tg = ctx.user_data.pop('adm_wset_tg', None)
    raw = (update.message.text or '').strip().replace(',', '').replace('،', '')
    if not raw.isdigit():
        await update.message.reply_text("فقط عدد غیرمنفی بفرست.")
        ctx.user_data['adm_wset_tg'] = tg
        return WAIT_WALLET_SET
    new_val = int(raw)
    profile = get_user_profile(telegram_id=tg)
    if not profile:
        await update.message.reply_text("کاربر پیدا نشد.")
        return ConversationHandler.END
    ok, old, new_bal, err = admin_set_wallet_balance(
        profile[0], new_val, desc=f'تنظیم دقیق توسط ادمین tg:{tg}'
    )
    if not ok:
        await update.message.reply_text(f"❌ {err}")
        return ConversationHandler.END
    try:
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=(
                f"💰 موجودی کیف پول تنظیم شد.\n"
                f"قبل: *{old:,}* ت\n"
                f"الان: *{new_bal:,}* ت"
            ),
            parse_mode='Markdown',
        )
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ موجودی: {old:,} → *{new_bal:,}* ت",
        parse_mode='Markdown',
        reply_markup=admin_user_keyboard(tg, profile[5]),
    )
    return ConversationHandler.END


async def admin_user_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    tg = query.data.replace('adm_ords_', '')
    profile = get_user_profile(telegram_id=tg)
    if not profile:
        await query.edit_message_text("کاربر پیدا نشد.")
        return
    from db import get_gem_infos_for_order
    handle = _tg_handle(profile[2])
    orders = get_user_orders(profile[0], limit=15)
    txs = list_wallet_txs(profile[0], limit=8)
    lines = [f"✦ سفارش‌های *{handle}*", f"`{tg}`", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    if not orders:
        lines.append("سفارشی نیست.")
    else:
        for o in orders:
            gems = get_gem_infos_for_order(o[0])
            uid = ''
            if gems and str(gems[0][2] or '').strip():
                uid = str(gems[0][2]).strip()
            uid_bit = f" · `{uid}`" if uid else ''
            lines.append(f"#{o[0]} · {o[1]:,} ت · `{o[2]}`{uid_bit}")
            buttons.append([InlineKeyboardButton(
                f'🔎 جزئیات #{o[0]}', callback_data=f'admx_orddetail_{o[0]}'
            )])
    lines.append("\nتراکنش کیف پول:")
    if not txs:
        lines.append("—")
    else:
        for t in txs:
            paid = "✓" if t[3] else "…"
            lines.append(f"{paid} {t[1]} {t[0]:,} — {(t[2] or '')[:36]}")
    buttons.extend([
        [InlineKeyboardButton('کارت کاربر', callback_data=f'adm_user_{tg}')],
        [InlineKeyboardButton('بازگشت', callback_data='adm_home')],
    ])
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_failed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    rows = list_failed_deliveries(15)
    if not rows:
        await query.edit_message_text(
            "✅ مورد ناموفقی نیست.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🔙 سفارش‌ها', callback_data='admx_hub_orders')],
            ]),
        )
        return
    lines = ["✦ *تحویل ناموفق*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    for r in rows:
        oid, tg, total, status, method, uid, g2st = r
        lines.append(
            f"#{oid} · {total:,} ت · `{status}`\n"
            f"  `{tg}` · uid `{uid}` · {g2st}"
        )
        buttons.append([
            InlineKeyboardButton(f'✅ انجام شد #{oid}', callback_data=f'adm_done_{oid}'),
            InlineKeyboardButton(f'🗑 لغو #{oid}', callback_data=f'adm_cancel_{oid}'),
        ])
        buttons.append([
            InlineKeyboardButton(f'🔁 تلاش مجدد #{oid}', callback_data=f'adm_retry_{oid}')
        ])
    buttons.append([InlineKeyboardButton('بازگشت به سفارش‌ها', callback_data='admx_hub_orders')])
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_open_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    rows = list_open_orders(15)
    lines = ["✦ *سفارش‌های باز*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    if not rows:
        lines.append("موردی نیست.")
    else:
        for r in rows:
            oid, tg, total, status, method, _created = r
            lines.append(f"#{oid} · {total:,} ت · `{status}` · `{method}` · `{tg}`")
            if status == 'pending':
                buttons.append([
                    InlineKeyboardButton(
                        f'🗑 لغو پرداخت‌نشده #{oid}', callback_data=f'adm_cancel_{oid}'
                    ),
                ])
            else:
                buttons.append([
                    InlineKeyboardButton(f'✅ انجام شد #{oid}', callback_data=f'adm_done_{oid}'),
                    InlineKeyboardButton(f'🗑 لغو+ریفاند #{oid}', callback_data=f'adm_cancel_{oid}'),
                ])
                buttons.append([
                    InlineKeyboardButton(f'🔁 تلاش مجدد #{oid}', callback_data=f'adm_retry_{oid}'),
                ])
    buttons.append([InlineKeyboardButton('بازگشت به سفارش‌ها', callback_data='admx_hub_orders')])
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("تلاش مجدد…")
    if not await _require_admin(update):
        return
    order_id = int(query.data.replace('adm_retry_', ''))
    order = get_order(order_id)
    if not order:
        await query.edit_message_text(
            "سفارش نیست",
            reply_markup=admin_home_keyboard(),
        )
        return
    success, status = await fulfill_order_async(order_id)
    tg = order[6]
    refunded = None
    if str(status).startswith('refunded:'):
        try:
            refunded = int(str(status).split(':', 1)[1])
        except (TypeError, ValueError):
            refunded = 0
    if success and status == 'delivered':
        msg = f"✅ سفارش #{order_id} تحویل شد."
        if tg:
            try:
                await ctx.bot.send_message(
                    chat_id=int(tg),
                    text=f"✅ سفارش #{order_id} تحویل شد.\n💎 جم به اکانتت واریز شد.",
                    reply_markup=main_menu(),
                )
            except Exception:
                pass
        try:
            await notify_admin(
                ctx.bot,
                f"✅ سفارش #{order_id} با تلاش مجدد ادمین تحویل شد.",
                parse_mode=None,
            )
        except Exception:
            pass
    elif refunded is not None:
        msg = (
            f"❌ سفارش #{order_id} ناموفق بود و لغو شد.\n"
            f"💰 مبلغ {refunded:,} ت به کیف پول کاربر برگشت."
        )
        if tg:
            try:
                await ctx.bot.send_message(
                    chat_id=int(tg),
                    text=(
                        f"❌ سفارش #{order_id} انجام نشد.\n"
                        f"💰 مبلغ {refunded:,} تومان به کیف پولت واریز شد."
                    ),
                    reply_markup=main_menu(),
                )
            except Exception:
                pass
    else:
        msg = f"⚠️ هنوز کامل نیست.\nوضعیت: `{status}`"
    await query.edit_message_text(
        msg, parse_mode='Markdown',
        reply_markup=admin_failed_order_keyboard(order_id, tg or ''),
    )


async def admin_mark_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """سفارشی که ادمین دستی تحویل داده را terminal 'delivered' کن."""
    query = update.callback_query
    await query.answer("در حال ثبت…")
    if not await _require_admin(update):
        return
    order_id = int(query.data.replace('adm_done_', ''))
    ok, status = await asyncio.to_thread(admin_mark_order_delivered, order_id)
    order = get_order(order_id)
    tg = order[6] if order else ''
    if ok and status in ('delivered', 'already'):
        msg = (
            f"✅ سفارش #{order_id} تحویل‌شده ثبت شد."
            if status == 'delivered'
            else f"ℹ️ سفارش #{order_id} از قبل تحویل‌شده بود."
        )
        # اگر پول این سفارش قبلاً (مثلاً با تلاش مجدد ناموفق) به کیف پول کاربر
        # برگشته، به ادمین یادآوری کن که موجودی کیف پول را کم کند.
        refunded = await asyncio.to_thread(order_refund_amount, order_id)
        if refunded > 0:
            msg += (
                f"\n\n⚠️ برای این سفارش {refunded:,} ت قبلاً به کیف پول کاربر "
                "برگشت. چون محصول تحویل شده، از کارت کاربر دکمه «➖ کسر کیف پول» "
                "را بزن و همین مبلغ را کسر کن."
            )
        if status == 'delivered' and tg:
            try:
                await ctx.bot.send_message(
                    chat_id=int(tg),
                    text=f"✅ سفارش #{order_id} توسط پشتیبانی تحویل و تکمیل شد.",
                    reply_markup=main_menu(),
                )
                await asyncio.to_thread(mark_delivery_notified, order_id, 'user')
                await asyncio.to_thread(mark_delivery_notified, order_id, 'admin')
            except Exception:
                pass
    elif status == 'busy':
        msg = f"⏳ سفارش #{order_id} در حال پردازش است؛ چند لحظه بعد دوباره تلاش کن."
    else:
        msg = f"❌ سفارش #{order_id} قابل ثبت به‌عنوان تحویل‌شده نیست."
    await _edit_safe(
        query, msg,
        reply_markup=admin_stuck_order_keyboard(order_id, str(tg) if tg else ''),
        parse_mode='Markdown',
    )


async def admin_stuck_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """لغو سفارش گیرکرده و بازپرداخت مبلغ به کیف پول کاربر."""
    query = update.callback_query
    await query.answer("در حال لغو…")
    if not await _require_admin(update):
        return
    order_id = int(query.data.replace('adm_cancel_', ''))
    ok, refunded, error = await asyncio.to_thread(
        admin_cancel_stuck_order, order_id
    )
    order = get_order(order_id)
    tg = order[6] if order else ''
    if ok:
        refund_line = (
            f"\n💰 مبلغ {refunded:,} تومان به کیف پول کاربر برگشت."
            if refunded > 0
            else "\n(مبلغی از کیف پول کسر نشده بود.)"
        )
        msg = f"🗑 سفارش #{order_id} لغو شد.{refund_line}"
        if tg:
            try:
                await ctx.bot.send_message(
                    chat_id=int(tg),
                    text=(
                        f"⚠️ سفارش #{order_id} لغو شد."
                        + (
                            f"\n💰 مبلغ {refunded:,} تومان به کیف پولت برگشت."
                            if refunded > 0
                            else ""
                        )
                    ),
                    reply_markup=main_menu(),
                )
            except Exception:
                pass
        await asyncio.to_thread(mark_delivery_notified, order_id, 'user')
        await asyncio.to_thread(mark_delivery_notified, order_id, 'admin')
    else:
        msg = f"❌ لغو انجام نشد: {error or 'وضعیت سفارش قابل لغو نیست.'}"
    await _edit_safe(
        query, msg,
        reply_markup=admin_stuck_order_keyboard(order_id, str(tg) if tg else ''),
        parse_mode='Markdown',
    )


async def admin_tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    rows = list_open_tickets(15)
    if not rows:
        await query.edit_message_text(
            "تیکت بازی نیست.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🔙 پشتیبانی', callback_data='admx_hub_support')],
            ]),
        )
        return
    lines = ["✦ *تیکت‌های باز*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    for r in rows:
        tid, subject, status, created, tg, name = r[:6]
        cat = r[6] if len(r) > 6 else 'other'
        tag = '🔐' if cat == 'credential' else '🎧'
        lines.append(f"{tag} #{tid} · {name or '—'} · `{tg}`\n  {(subject or '')[:50]}")
        buttons.append([
            InlineKeyboardButton(f'#{tid} پاسخ', callback_data=f'adm_treply_{tid}'),
            InlineKeyboardButton('بستن', callback_data=f'adm_tclose_{tid}'),
        ])
    buttons.append([InlineKeyboardButton('🔙 پشتیبانی', callback_data='admx_hub_support')])
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_credential_tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """تیکت‌های فقط بخش جم با اطلاعات — برای پشتیبان credential و ادمین کامل."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not (is_admin(uid) or is_credential_admin(uid)):
        await query.edit_message_text(_deny_text())
        return
    rows = list_open_tickets(20, category='credential')
    back = 'admx_credhub' if is_credential_admin(uid) and not is_admin(uid) else 'admx_hub_support'
    if not rows:
        await query.edit_message_text(
            "تیکت باز جم با اطلاعات نیست.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('🔙 بازگشت', callback_data=back)],
            ]),
        )
        return
    lines = ["🔐 *تیکت‌های جم با اطلاعات*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    for r in rows:
        tid, subject, status, created, tg, name = r[:6]
        lines.append(f"#{tid} · {name or '—'} · `{tg}`\n  {(subject or '')[:50]}")
        buttons.append([
            InlineKeyboardButton(f'#{tid} پاسخ', callback_data=f'adm_treply_{tid}'),
            InlineKeyboardButton('بستن', callback_data=f'adm_tclose_{tid}'),
        ])
    buttons.append([InlineKeyboardButton('🔙 بازگشت', callback_data=back)])
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_ticket_reply_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.replace('adm_treply_', ''))
    ticket = get_ticket(tid)
    if not ticket:
        await query.edit_message_text("تیکت پیدا نشد.")
        return ConversationHandler.END
    if not await _can_manage_ticket(update, ticket):
        return ConversationHandler.END
    ctx.user_data['adm_ticket_id'] = tid
    ctx.user_data['adm_ticket_category'] = _ticket_category(ticket)
    tg = ticket[5] or ticket[6]
    await query.edit_message_text(
        f"پاسخ تیکت #{tid}\nکاربر `{tg}`\n{(ticket[3] or '')[:280]}\n\nپاسخت را بفرست:",
        parse_mode='Markdown',
    )
    return WAIT_TICKET_REPLY


async def admin_ticket_reply_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tid = ctx.user_data.get('adm_ticket_id')
    if not tid:
        return ConversationHandler.END
    ticket = get_ticket(tid)
    if not ticket or not await _can_manage_ticket(update, ticket):
        ctx.user_data.pop('adm_ticket_id', None)
        return ConversationHandler.END
    ctx.user_data.pop('adm_ticket_id', None)
    cat = _ticket_category(ticket)
    text = update.message.text or ''
    add_ticket_message(tid, 'admin', text)
    tg = ticket[5] or ticket[6]
    back_to = 'admx_credhub' if cat == 'credential' else 'admx_hub_support'
    # پشتیبان credential دکمه کارت کاربر نگیرد
    show_user = is_admin(uid)
    try:
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=f"🎧 *پاسخ پشتیبانی — تیکت #{tid}*\n\n{text}",
            parse_mode='Markdown',
            reply_markup=main_menu(),
        )
        await update.message.reply_text(
            "✅ ارسال شد.",
            reply_markup=admin_ticket_keyboard(
                tid, tg if show_user else None, back_to=back_to,
            ),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")
    return ConversationHandler.END


async def admin_ticket_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("بسته شد")
    tid = int(query.data.replace('adm_tclose_', ''))
    ticket = get_ticket(tid)
    if not await _can_manage_ticket(update, ticket):
        return
    close_ticket(tid)
    tg = (ticket[5] or ticket[6]) if ticket else None
    if tg:
        try:
            await ctx.bot.send_message(
                chat_id=int(tg),
                text=f"✅ تیکت #{tid} بسته شد.",
                reply_markup=main_menu(),
            )
        except Exception:
            pass
    uid = update.effective_user.id
    cat = _ticket_category(ticket)
    if is_credential_admin(uid) and not is_admin(uid):
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton('🔙 پنل جم با اطلاعات', callback_data='admx_credhub')],
        ])
    elif cat == 'credential':
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton('🔙 تیکت‌های این بخش', callback_data='adm_cred_tickets')],
        ])
    else:
        markup = admin_home_keyboard()
    await query.edit_message_text(f"تیکت #{tid} بسته شد.", reply_markup=markup)


async def admin_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('adm_msg_tg', None)
    ctx.user_data.pop('adm_wal_tg', None)
    ctx.user_data.pop('adm_wal_mode', None)
    ctx.user_data.pop('adm_wset_tg', None)
    ctx.user_data.pop('adm_ticket_id', None)
    if update.message:
        if is_admin(update.effective_user.id):
            await update.message.reply_text("انصراف.", reply_markup=admin_home_keyboard())
        elif is_credential_admin(update.effective_user.id):
            await update.message.reply_text(
                "انصراف.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton('🔙 پنل جم با اطلاعات', callback_data='admx_credhub'),
                ]]),
            )
        else:
            await update.message.reply_text(_deny_text())
    return ConversationHandler.END


def admin_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_find_start, pattern='^adm_find$'),
            CallbackQueryHandler(admin_msg_start, pattern=r'^adm_msg_\d+$'),
            CallbackQueryHandler(admin_wallet_start, pattern=r'^adm_wal_\d+$'),
            CallbackQueryHandler(admin_wallet_deduct_start, pattern=r'^adm_wdeduct_\d+$'),
            CallbackQueryHandler(admin_wallet_set_start, pattern=r'^adm_wset_\d+$'),
            CallbackQueryHandler(admin_ticket_reply_start, pattern=r'^adm_treply_\d+$'),
        ],
        states={
            WAIT_FIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_find_recv)],
            WAIT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_msg_send)],
            WAIT_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_wallet_apply)],
            WAIT_WALLET_SET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_wallet_set_apply)
            ],
            WAIT_TICKET_REPLY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ticket_reply_send)
            ],
        },
        fallbacks=[CommandHandler('cancel', admin_cancel)],
        allow_reentry=True,
    )
