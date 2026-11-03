"""«🕹 شارژ بازی‌ها»: پابجی، کالاف دیوتی، موبایل لجندز و بقیه — یک دکمه در منو.

بازی ← بسته ← آیدی بازیکن (و سرور اگر لازم است) ← نام بازیکن از تامین‌کننده ←
تایید ← همان صفحهٔ پرداختِ بقیهٔ ربات. تحویل خودکار در games_db.
"""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

import appearance
import g2bulk
import games_db
from db import get_bool_setting, get_or_create_user, get_wallet_balance
from keyboards import main_menu, pay_method_keyboard
from payment_safety import checked_amount
from text_safety import markdown_safe

GAME_PLAYER, GAME_SERVER, GAME_CONFIRM = range(3)
GAMES_PER_PAGE = 10
_BACK_HOME = [InlineKeyboardButton('🔙 منوی اصلی', callback_data='home')]


def _md(text, limit=160):
    return markdown_safe(text, limit)


def _games_keyboard(games, page):
    total_pages = max(1, (len(games) + GAMES_PER_PAGE - 1) // GAMES_PER_PAGE)
    page = max(1, min(page, total_pages))
    chunk = games[(page - 1) * GAMES_PER_PAGE: page * GAMES_PER_PAGE]
    rows, row = [], []
    for game in chunk:
        row.append(InlineKeyboardButton(f"🎮 {game['title']}", callback_data=f"gm_g_{game['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton('◀️ قبلی', callback_data=f'gm_page_{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton('بعدی ▶️', callback_data=f'gm_page_{page + 1}'))
    if nav:
        rows.append(nav)
    rows.append(_BACK_HOME)
    return InlineKeyboardMarkup(rows), page, total_pages


def _packages_keyboard(game, packages):
    rows = [[InlineKeyboardButton(f"{p['title']} — {p['price']:,} ت", callback_data=f"gm_buy_{p['id']}")]
            for p in packages]
    rows.append([InlineKeyboardButton('🔙 همهٔ بازی‌ها', callback_data='gm_home')])
    return InlineKeyboardMarkup(rows)


async def _show(update, text, markup):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=markup)


async def games_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    games = await asyncio.to_thread(games_db.list_games)
    page = 1
    data = str(getattr(update.callback_query, 'data', '') or '')
    if data.startswith('gm_page_'):
        page = int(data.rsplit('_', 1)[-1])
    if not games:
        await _show(update, '🕹 *شارژ بازی‌ها*\n━━━━━━━━━━━━━━━\n'
                            '❌ فهرست بازی‌ها همین حالا به‌روز می‌شود؛ کمی بعد دوباره سر بزن.',
                    InlineKeyboardMarkup([_BACK_HOME]))
        return
    markup, page, total = _games_keyboard(games, page)
    text = ('🕹 *شارژ بازی‌ها با آیدی*\n━━━━━━━━━━━━━━━\n'
            'یوسی پابجی، سی‌پی کالاف، الماس موبایل لجندز و بقیه — فقط با آیدی بازیکن، '
            'بدون رمز اکانت، تحویل خودکار.\n\nبازی را انتخاب کن:')
    if total > 1:
        text += f'  (صفحهٔ {page} از {total})'
    await _show(update, text, markup)


async def game_packages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    game_id = int(update.callback_query.data.rsplit('_', 1)[-1])
    game = await asyncio.to_thread(games_db.get_game, game_id)
    packages = await asyncio.to_thread(games_db.list_game_packages, game_id) if game else []
    if not game or not packages:
        await update.callback_query.answer('این بازی فعلاً بسته‌ای ندارد.', show_alert=True)
        return
    text = f"🎮 *{_md(game['title'])}*\n━━━━━━━━━━━━━━━\n"
    if game['unit']:
        text += f"بسته‌های {_md(game['unit'])}، فقط با آیدی بازیکن.\n"
    if game['warning']:
        text += f"\n⚠️ {_md(game['warning'], 400)}\n"
    text += '\nبسته را انتخاب کن:'
    await _show(update, text, _packages_keyboard(game, packages))


def _cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton('✖️ انصراف', callback_data='gm_x')]])


async def game_buy_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pkg = await asyncio.to_thread(games_db.get_game_package, int(query.data.rsplit('_', 1)[-1]))
    game = await asyncio.to_thread(games_db.get_game, pkg['game_id']) if pkg else None
    if not pkg or not pkg['available'] or not game:
        await query.edit_message_text('❌ این بسته در دسترس نیست.')
        return ConversationHandler.END
    ctx.user_data['game_buy'] = {'pkg': pkg['id'], 'price': pkg['price'], 'title': pkg['title'],
                                 'game_id': game['id'], 'game': game['title'], 'code': game['code'],
                                 'needs_server': game['needs_server'], 'servers': game['servers']}
    await query.edit_message_text(
        f"🆔 *{_md(pkg['title'])} — {_md(game['title'])}*\n━━━━━━━━━━━━━━━\n"
        f"💰 {pkg['price']:,} تومان\n\n"
        'آیدی عددی بازیکن (UID) را بفرست. از پروفایل داخل بازی کپی کن؛ نام نمایشی قبول نیست.',
        parse_mode='Markdown', reply_markup=_cancel_kb())
    return GAME_PLAYER


async def _ask_confirm(update, ctx, *, via_query=False):
    info = ctx.user_data['game_buy']
    wait = None
    if via_query:
        await update.callback_query.edit_message_text('⏳ در حال بررسی آیدی…')
    else:
        wait = await update.message.reply_text('⏳ در حال بررسی آیدی…')
    name = ''
    if g2bulk.is_configured():
        result = await asyncio.to_thread(g2bulk.check_player_id, info['player'], info['code'],
                                         info.get('server') or None)
        if result.get('ok') and result.get('name') and result['name'] != info['player']:
            name = str(result['name'])
    info['player_name'] = name
    text = ('✅ *آیدی ثبت شد*\n━━━━━━━━━━━━━━━\n'
            f"🎮 {_md(info['game'])} — {_md(info['title'])}\n"
            f"🆔 آیدی: `{info['player']}`\n")
    if info.get('server'):
        text += f"🌐 سرور: `{_md(info['server'], 60)}`\n"
    if name:
        text += f"👤 نام بازیکن: *{_md(name)}*\n"
    text += f"💰 مبلغ: *{int(info['price']):,} تومان*\n\nاگر درست است تایید کن تا بری سراغ پرداخت."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ تایید و ادامه پرداخت', callback_data='gm_ok')],
        [InlineKeyboardButton('✏️ اصلاح آیدی', callback_data='gm_re')],
        [InlineKeyboardButton('✖️ انصراف', callback_data='gm_x')],
    ])
    if via_query:
        await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)
    else:
        await wait.edit_text(text, parse_mode='Markdown', reply_markup=kb)
    return GAME_CONFIRM


async def game_get_player(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or '').strip()
    info = ctx.user_data.get('game_buy')
    if not info or raw in appearance.all_menu_labels():
        ctx.user_data.pop('game_buy', None)
        await update.message.reply_text('✖️ ثبت سفارش لغو شد.', reply_markup=main_menu())
        return ConversationHandler.END
    player = games_db.normalize_player_id(raw)
    if not player:
        await update.message.reply_text('⚠️ آیدی معتبر نیست؛ فقط آیدی عددی بازیکن را بفرست:',
                                        reply_markup=_cancel_kb())
        return GAME_PLAYER
    info['player'] = player
    if info.get('needs_server'):
        servers = info.get('servers') or []
        if servers:
            rows = [[InlineKeyboardButton(s, callback_data=f'gm_srv_{i}')]
                    for i, s in enumerate(servers[:40])]
            rows.append([InlineKeyboardButton('✖️ انصراف', callback_data='gm_x')])
            await update.message.reply_text('🌐 سرور اکانتت را انتخاب کن:',
                                            reply_markup=InlineKeyboardMarkup(rows))
        else:
            await update.message.reply_text('🌐 شناسهٔ سرور (Zone ID) را بفرست:',
                                            reply_markup=_cancel_kb())
        return GAME_SERVER
    return await _ask_confirm(update, ctx)


async def game_pick_server(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('game_buy')
    servers = (info or {}).get('servers') or []
    index = int(query.data.rsplit('_', 1)[-1])
    if not info or index >= len(servers):
        await query.edit_message_text('❌ جلسه سفارش منقضی شد. دوباره از منو شروع کن.')
        return ConversationHandler.END
    info['server'] = servers[index]
    return await _ask_confirm(update, ctx, via_query=True)


async def game_type_server(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    info = ctx.user_data.get('game_buy')
    server = games_db.normalize_player_id(update.message.text)
    if not info or not server:
        await update.message.reply_text('⚠️ شناسهٔ سرور معتبر نیست؛ دوباره بفرست:', reply_markup=_cancel_kb())
        return GAME_SERVER
    info['server'] = server
    return await _ask_confirm(update, ctx)


async def game_reedit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text('✏️ آیدی بازیکن را دوباره بفرست:', reply_markup=_cancel_kb())
    return GAME_PLAYER


async def game_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    info = ctx.user_data.get('game_buy')
    if not info or not info.get('player'):
        await query.edit_message_text('❌ اطلاعات سفارش ناقص است.')
        return ConversationHandler.END
    if not await asyncio.to_thread(get_bool_setting, 'sales_enabled', True):
        await query.edit_message_text('⛔ فروش موقتاً توسط مدیریت متوقف شده است.')
        return ConversationHandler.END
    await query.edit_message_text('⏳ *در حال بررسی نهایی موجودی و قیمت…*', parse_mode='Markdown')
    current = await asyncio.to_thread(games_db.get_game_package, info['pkg'])
    try:
        price = checked_amount((current or {}).get('price'), label='قیمت بسته')
    except ValueError:
        ctx.user_data.pop('game_buy', None)
        await query.edit_message_text('❌ این بسته دیگر موجود نیست. دوباره از فهرست انتخاب کن.')
        return ConversationHandler.END
    user = update.effective_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or 'کاربر تلگرام'

    def persist():
        db_id, _ = get_or_create_user(user.id, user.first_name or '', user.last_name or '',
                                      user.username or '')
        order_id, title, total = games_db.create_game_order_atomic(
            db_id, info['pkg'], price, player_id=info['player'], server_id=info.get('server') or '',
            player_name=info.get('player_name') or '', telegram_id=user.id, full_name=full_name)
        return db_id, order_id, title, total, int(get_wallet_balance(db_id) or 0)

    try:
        db_id, order_id, title, total, balance = await asyncio.to_thread(persist)
    except ValueError as exc:
        ctx.user_data.pop('game_buy', None)
        await query.edit_message_text(f'❌ {exc}')
        return ConversationHandler.END
    ctx.user_data['db_id'] = db_id
    ctx.user_data['pending_order'] = {'order_id': order_id, 'total': total, 'title': title, 'tg_id': user.id}
    ctx.user_data.pop('game_buy', None)
    await query.edit_message_text(
        f"✦ *انتخاب روش پرداخت*\nسفارش `#{order_id}`\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"{_md(title, 120)}\nآیدی `{info['player']}`\n"
        f"مبلغ: *{total:,}* تومان\nموجودی کیف پول: *{balance:,}* ت\n"
        "⚡ بعد از تأیید پرداخت، شارژ خودکار روی همین آیدی می‌نشیند.\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄\nروش را انتخاب کن:",
        parse_mode='Markdown',
        reply_markup=pay_method_keyboard(order_id, can_wallet=True, wallet_balance=balance,
                                         remaining=total))
    return ConversationHandler.END


async def game_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('game_buy', None)
    query = update.callback_query
    if query:
        await query.answer('لغو شد')
        await query.edit_message_text('✖️ ثبت سفارش لغو شد.')
    else:
        await update.message.reply_text('✖️ لغو شد.', reply_markup=main_menu())
    return ConversationHandler.END


def games_conversation_handler():
    text = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(game_buy_start, pattern=r'^gm_buy_\d+$')],
        states={
            GAME_PLAYER: [MessageHandler(text, game_get_player),
                          CallbackQueryHandler(game_cancel, pattern='^gm_x$')],
            GAME_SERVER: [CallbackQueryHandler(game_pick_server, pattern=r'^gm_srv_\d+$'),
                          MessageHandler(text, game_type_server),
                          CallbackQueryHandler(game_cancel, pattern='^gm_x$')],
            GAME_CONFIRM: [CallbackQueryHandler(game_confirm, pattern='^gm_ok$'),
                           CallbackQueryHandler(game_reedit, pattern='^gm_re$'),
                           CallbackQueryHandler(game_cancel, pattern='^gm_x$')],
        },
        fallbacks=[CallbackQueryHandler(game_cancel, pattern='^gm_x$')],
        per_message=False,
        allow_reentry=True,
    )


def games_callback_handlers():
    return [
        CallbackQueryHandler(games_menu, pattern=r'^(?:gm_home|gm_page_\d+)$'),
        CallbackQueryHandler(game_packages, pattern=r'^gm_g_\d+$'),
    ]
