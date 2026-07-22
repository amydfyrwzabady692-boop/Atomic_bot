"""پنل مدیریت ادمین — فقط برای ADMIN_CHAT_ID."""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, CommandHandler, filters,
)

from admin_notify import is_admin
from keyboards import (
    admin_home_keyboard, admin_user_keyboard, admin_failed_order_keyboard,
    admin_ticket_keyboard, main_menu,
)
from db import (
    get_admin_stats, list_recent_users, get_user_profile, find_user_by_username,
    set_user_blocked, list_failed_deliveries, list_open_orders, admin_adjust_wallet,
    list_wallet_txs, get_user_orders, fulfill_order, get_order,
    list_open_tickets, get_ticket, close_ticket, add_ticket_message,
)

WAIT_FIND = 1
WAIT_MSG = 2
WAIT_WALLET = 3
WAIT_TICKET_REPLY = 4


def _deny_text():
    return "❌ این دستور برای شما فعال نیست."


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
    text = (
        f"✦ *پنل ادمین Atomic*\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"کاربران: *{s['users']:,}*  ·  بلاک: {s['blocked']}\n"
        f"سفارش‌ها: *{s['orders']:,}*  ·  باز: {s['open_orders']}\n"
        f"تحویل ناموفق: *{s['failed_g2']:,}*\n"
        f"تیکت باز: *{s['open_tickets']:,}*\n"
        f"مجموع کیف پول‌ها: *{s['wallet_sum']:,}* ت"
    )
    kb = admin_home_keyboard()
    if via_message:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=kb
        )


async def admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    rows = list_recent_users(15)
    lines = ["✦ *آخرین کاربران*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    for r in rows:
        _db_id, tg, name, uname, blocked, bal = r
        handle = _tg_handle(uname)
        flag = "🚫" if blocked else "·"
        lines.append(
            f"{flag} *{handle}*  ·  {name or '—'}\n"
            f"   `{tg}`  ·  {bal:,} ت"
        )
        label = f"{'🚫 ' if blocked else ''}{handle if handle != '—' else (name or tg)}"
        buttons.append([InlineKeyboardButton(label[:40], callback_data=f'adm_user_{tg}')])
    lines.append("\nجستجو با آیدی `@user` یا شناسه عددی")
    buttons.append([InlineKeyboardButton('🔎 جستجو', callback_data='adm_find')])
    buttons.append([InlineKeyboardButton('بازگشت', callback_data='adm_home')])
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
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
    await query.edit_message_text(
        f"💰 تنظیم کیف پول `{tg}`\n"
        f"عدد مثبت = شارژ · منفی = کسر\n"
        f"مثال: `50000` یا `-20000`\n/cancel",
        parse_mode='Markdown',
    )
    return WAIT_WALLET


async def admin_wallet_apply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    tg = ctx.user_data.pop('adm_wal_tg', None)
    raw = (update.message.text or '').strip().replace(',', '').replace('،', '')
    if not raw.lstrip('-').isdigit():
        await update.message.reply_text("فقط عدد بفرست.")
        ctx.user_data['adm_wal_tg'] = tg
        return WAIT_WALLET
    amount = int(raw)
    profile = get_user_profile(telegram_id=tg)
    if not profile:
        await update.message.reply_text("کاربر پیدا نشد.")
        return ConversationHandler.END
    ok, new_bal, err = admin_adjust_wallet(profile[0], amount, desc=f'ادمین → tg:{tg}')
    if not ok:
        await update.message.reply_text(f"❌ {err}")
        return ConversationHandler.END
    try:
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=(
                f"💰 موجودی کیف پول تغییر کرد.\n"
                f"تغییر: *{amount:+,}* ت\n"
                f"موجودی: *{new_bal:,}* ت"
            ),
            parse_mode='Markdown',
        )
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ موجودی جدید: *{new_bal:,}* ت",
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
    handle = _tg_handle(profile[2])
    orders = get_user_orders(profile[0], limit=15)
    txs = list_wallet_txs(profile[0], limit=8)
    lines = [f"✦ سفارش‌های *{handle}*", f"`{tg}`", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    if not orders:
        lines.append("سفارشی نیست.")
    else:
        for o in orders:
            lines.append(f"#{o[0]} · {o[1]:,} ت · `{o[2]}`")
    lines.append("\nتراکنش کیف پول:")
    if not txs:
        lines.append("—")
    else:
        for t in txs:
            paid = "✓" if t[3] else "…"
            lines.append(f"{paid} {t[1]} {t[0]:,} — {(t[2] or '')[:36]}")
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('کارت کاربر', callback_data=f'adm_user_{tg}')],
            [InlineKeyboardButton('بازگشت', callback_data='adm_home')],
        ]),
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
            reply_markup=admin_home_keyboard(),
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
            InlineKeyboardButton(f'تلاش مجدد #{oid}', callback_data=f'adm_retry_{oid}')
        ])
    buttons.append([InlineKeyboardButton('بازگشت', callback_data='adm_home')])
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_open_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    rows = list_open_orders(20)
    lines = ["✦ *سفارش‌های باز*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    if not rows:
        lines.append("موردی نیست.")
    else:
        for r in rows:
            lines.append(f"#{r[0]} · {r[2]:,} ت · `{r[3]}` · `{r[1]}`")
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown', reply_markup=admin_home_keyboard()
    )


async def admin_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("تلاش مجدد…")
    if not await _require_admin(update):
        return
    order_id = int(query.data.replace('adm_retry_', ''))
    order = get_order(order_id)
    if not order:
        await query.answer("سفارش نیست", show_alert=True)
        return
    success, status = fulfill_order(order_id)
    tg = order[6]
    if success and status == 'delivered':
        msg = f"✅ سفارش #{order_id} تحویل شد."
        if tg:
            try:
                await ctx.bot.send_message(
                    chat_id=int(tg),
                    text=f"✅ سفارش #{order_id} تحویل شد.",
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


async def admin_tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return
    rows = list_open_tickets(15)
    if not rows:
        await query.edit_message_text(
            "تیکت بازی نیست.", reply_markup=admin_home_keyboard()
        )
        return
    lines = ["✦ *تیکت‌های باز*", "┄┄┄┄┄┄┄┄┄┄┄┄┄┄"]
    buttons = []
    for r in rows:
        tid, subject, status, created, tg, name = r
        lines.append(f"#{tid} · {name or '—'} · `{tg}`\n  {(subject or '')[:50]}")
        buttons.append([
            InlineKeyboardButton(f'#{tid} پاسخ', callback_data=f'adm_treply_{tid}'),
            InlineKeyboardButton('بستن', callback_data=f'adm_tclose_{tid}'),
        ])
    buttons.append([InlineKeyboardButton('بازگشت', callback_data='adm_home')])
    await query.edit_message_text(
        "\n".join(lines), parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_ticket_reply_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _require_admin(update):
        return ConversationHandler.END
    tid = int(query.data.replace('adm_treply_', ''))
    ticket = get_ticket(tid)
    if not ticket:
        await query.edit_message_text("تیکت پیدا نشد.")
        return ConversationHandler.END
    ctx.user_data['adm_ticket_id'] = tid
    tg = ticket[5] or ticket[6]
    await query.edit_message_text(
        f"پاسخ تیکت #{tid}\nکاربر `{tg}`\n{(ticket[3] or '')[:280]}\n\nپاسخت را بفرست:",
        parse_mode='Markdown',
    )
    return WAIT_TICKET_REPLY


async def admin_ticket_reply_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    tid = ctx.user_data.pop('adm_ticket_id', None)
    if not tid:
        return ConversationHandler.END
    ticket = get_ticket(tid)
    if not ticket:
        await update.message.reply_text("تیکت پیدا نشد.")
        return ConversationHandler.END
    text = update.message.text or ''
    add_ticket_message(tid, 'admin', text)
    tg = ticket[5] or ticket[6]
    try:
        await ctx.bot.send_message(
            chat_id=int(tg),
            text=f"🎧 *پاسخ پشتیبانی — تیکت #{tid}*\n\n{text}",
            parse_mode='Markdown',
            reply_markup=main_menu(),
        )
        await update.message.reply_text(
            "✅ ارسال شد.",
            reply_markup=admin_ticket_keyboard(tid),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")
    return ConversationHandler.END


async def admin_ticket_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("بسته شد")
    if not await _require_admin(update):
        return
    tid = int(query.data.replace('adm_tclose_', ''))
    close_ticket(tid)
    ticket = get_ticket(tid)
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
    await query.edit_message_text(f"تیکت #{tid} بسته شد.", reply_markup=admin_home_keyboard())


async def admin_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('adm_msg_tg', None)
    ctx.user_data.pop('adm_wal_tg', None)
    ctx.user_data.pop('adm_ticket_id', None)
    if update.message:
        if is_admin(update.effective_user.id):
            await update.message.reply_text("انصراف.", reply_markup=admin_home_keyboard())
        else:
            await update.message.reply_text(_deny_text())
    return ConversationHandler.END


def admin_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_find_start, pattern='^adm_find$'),
            CallbackQueryHandler(admin_msg_start, pattern=r'^adm_msg_\d+$'),
            CallbackQueryHandler(admin_wallet_start, pattern=r'^adm_wal_\d+$'),
            CallbackQueryHandler(admin_ticket_reply_start, pattern=r'^adm_treply_\d+$'),
        ],
        states={
            WAIT_FIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_find_recv)],
            WAIT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_msg_send)],
            WAIT_WALLET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_wallet_apply)],
            WAIT_TICKET_REPLY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ticket_reply_send)
            ],
        },
        fallbacks=[CommandHandler('cancel', admin_cancel)],
        allow_reentry=True,
    )
