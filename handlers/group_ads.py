"""پنل «👥 گروه‌ها و تبلیغ»: گروه‌هایی که ربات عضوشان است، و تبلیغ ۱۲ ساعته."""
import asyncio
import html
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes, TypeHandler

import group_ads
from admin_notify import is_admin, notify_admin

_GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)
_seen = {}


async def on_group_membership(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ربات به گروهی اضافه یا از آن حذف شد."""
    cm = update.my_chat_member
    chat = getattr(cm, 'chat', None)
    if not cm or not chat or chat.type not in _GROUP_TYPES:
        return
    status = getattr(cm.new_chat_member, 'status', '')
    member = status in ('member', 'administrator', 'creator', 'restricted')
    await asyncio.to_thread(group_ads.remember_group, chat.id, chat.title or '',
                            chat.username or '', chat.type, member)
    title = html.escape(chat.title or str(chat.id))
    if member:
        text = (f'👥 ربات به گروه <b>{title}</b> اضافه شد.\n'
                f'تبلیغ اتومیک شاپ هر {group_ads.interval_hours()} ساعت آن‌جا می‌رود؛ '
                'از پنل ← «👥 گروه‌ها و تبلیغ» خاموش/روشنش کن.')
    else:
        text = f'👋 ربات از گروه <b>{title}</b> خارج شد؛ دیگر تبلیغی آن‌جا نمی‌رود.'
    try:
        await notify_admin(ctx.bot, text, parse_mode='HTML')
    except TypeError:
        await notify_admin(ctx.bot, html.unescape(text.replace('<b>', '').replace('</b>', '')))
    except Exception:  # noqa: BLE001
        pass


async def remember_seen_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """گروهی که ربات پیش از این نسخه عضوش بوده، با اولین پیام/دستورِ آن‌جا ثبت می‌شود."""
    chat = update.effective_chat
    if not chat or chat.type not in _GROUP_TYPES:
        return
    message = update.effective_message
    if message is not None and getattr(message, 'migrate_to_chat_id', None):
        await asyncio.to_thread(group_ads.migrate_group, chat.id, message.migrate_to_chat_id)
        return
    now = time.monotonic()
    if now - _seen.get(chat.id, 0) < 3600:
        return
    _seen[chat.id] = now
    try:
        await asyncio.to_thread(group_ads.remember_group, chat.id, chat.title or '',
                                chat.username or '', chat.type, True)
    except Exception:  # noqa: BLE001 — ثبت گروه نباید هیچ پیامی را خراب کند
        pass


def _panel_keyboard(groups):
    rows = []
    for group in groups[:30]:
        if not group['is_member']:
            continue
        mark = '🟢' if group['ads'] else '⏸'
        rows.append([InlineKeyboardButton(f"{mark} {group['title'][:40] or group['chat_id']}",
                                          callback_data=f"grp_t_{group['chat_id']}")])
    rows.append([InlineKeyboardButton('📣 همین حالا در همه بفرست', callback_data='grp_now'),
                 InlineKeyboardButton('👁 پیش‌نمایش', callback_data='grp_prev')])
    rows.append([InlineKeyboardButton('🔙 پنل', callback_data='adm_home')])
    return InlineKeyboardMarkup(rows)


def _panel_text(groups):
    member = [g for g in groups if g['is_member']]
    lines = [f'👥 <b>گروه‌ها و تبلیغ</b> — هر {group_ads.interval_hours()} ساعت، پیام قبلی پاک می‌شود',
             f'گروه‌هایی که ربات عضوشان است: <b>{len(member)}</b>', '']
    lines += [group_ads.status_line(g) for g in groups[:30]] or ['هنوز گروهی ثبت نشده.']
    lines += ['', 'روی هر گروه بزن تا تبلیغش روشن/خاموش شود.',
              'گروهی که ربات از قبل در آن بوده، با اولین پیام یا دستوری که ربات آن‌جا ببیند '
              '(مثلاً ‎/start‎) ثبت می‌شود.',
              'متن تبلیغ: ‎/adtext متن‌جدید‎ — برگشت به متن پیش‌فرض: ‎/adtext reset‎']
    return '\n'.join(lines)


async def groups_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
        await query.answer('فقط مدیر', show_alert=True)
        return
    data = query.data or ''
    note = None
    if data.startswith('grp_t_'):
        state = await asyncio.to_thread(group_ads.toggle_ads, int(data[len('grp_t_'):]))
        note = 'تبلیغ روشن شد' if state else 'تبلیغ خاموش شد'
    elif data == 'grp_now':
        await query.answer('در حال ارسال…')
        sent = await group_ads.run_due(ctx.bot, force=True)
        note = f'در {sent} گروه فرستاده شد'
    elif data == 'grp_prev':
        text = await asyncio.to_thread(group_ads.ad_text)
        try:
            await ctx.bot.send_message(update.effective_user.id, text, parse_mode='HTML',
                                       reply_markup=group_ads.ad_keyboard(ctx.bot.username),
                                       disable_web_page_preview=True)
        except Exception:  # noqa: BLE001 — HTML نامعتبر در متن سفارشی
            await ctx.bot.send_message(update.effective_user.id, html.unescape(text),
                                       reply_markup=group_ads.ad_keyboard(ctx.bot.username))
        note = 'پیش‌نمایش فرستاده شد'
    if data != 'grp_now':
        await query.answer(note or '')
    groups = await asyncio.to_thread(group_ads.list_groups)
    try:
        await query.edit_message_text(_panel_text(groups), parse_mode='HTML',
                                      reply_markup=_panel_keyboard(groups))
    except Exception:  # noqa: BLE001 — «message is not modified»
        pass


async def adtext_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
        return
    raw = (update.message.text or '').split(maxsplit=1)
    arg = raw[1].strip() if len(raw) > 1 else ''
    if not arg:
        current = await asyncio.to_thread(group_ads.ad_text)
        await update.message.reply_text(
            'متن فعلی تبلیغ گروه‌ها پایین است. برای عوض‌کردن: ‎/adtext متن‌جدید‎ '
            '(HTML مثل <b>پررنگ</b> مجاز است)؛ برای متن پیش‌فرض: ‎/adtext reset‎')
        await update.message.reply_text(current, parse_mode='HTML',
                                        reply_markup=group_ads.ad_keyboard(ctx.bot.username))
        return
    await asyncio.to_thread(group_ads.set_ad_text, '' if arg.lower() == 'reset' else arg)
    await update.message.reply_text('✅ متن تبلیغ ذخیره شد؛ از نوبت بعد در گروه‌ها می‌رود.')


def group_ads_handlers():
    """(handler, group) — ثبت گروه در گروه -3 تا هیچ مسیر دیگری را متوقف نکند."""
    return [
        (TypeHandler(Update, remember_seen_group), -3),
        (ChatMemberHandler(on_group_membership, ChatMemberHandler.MY_CHAT_MEMBER), 1),
        (CallbackQueryHandler(groups_panel, pattern=r'^grp_(?:home|now|prev|t_-?\d+)$'), 0),
        (CommandHandler('adtext', adtext_command), 0),
    ]
