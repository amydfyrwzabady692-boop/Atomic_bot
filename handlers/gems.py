from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler,
    CallbackQueryHandler, filters
)
from keyboards import (
    gem_type_keyboard, gems_keyboard, gem_detail_keyboard,
    gem_login_method_keyboard, gem_skip_keyboard, gem_cancel_keyboard,
    added_to_cart_keyboard, main_menu, PLAN_LABELS,
)
from db import get_gems, get_gem
from handlers.cart import cart_add, _get_cart, _cart_total

# مراحل گفتگوی ثبت سفارش جم
GEM_UID, GEM_METHOD, GEM_EMAIL, GEM_PASS, GEM_BACKUP = range(5)

PTYPE_TITLE = {
    'by_id': '🆔 خرید با آیدی بازی (UID)',
    'by_credentials': '🔐 خرید با اطلاعات اکانت',
}
METHOD_LABELS = {'gmail': 'جیمیل', 'facebook': 'فیسبوک', 'vk': 'VK'}


# ─── انتخاب روش خرید ─────────────────────────────────────────────────────────────
async def gems_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "💎 *جم فری‌فایر — Atomic Shop*\n"
        "━━━━━━━━━━━━━━━\n"
        "شارژ مستقیم و امن الماس فری‌فایر ⚡️\n"
        "_تحویل سریع پس از تأیید پرداخت_\n\n"
        "اول روش خریدت رو انتخاب کن:\n"
        "🆔 *با آیدی* — فقط UID بازی رو می‌دی\n"
        "🔐 *با اطلاعات اکانت* — ورود به اکانتت برای شارژ"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode='Markdown', reply_markup=gem_type_keyboard()
        )
    else:
        await update.message.reply_text(
            text, parse_mode='Markdown', reply_markup=gem_type_keyboard()
        )


async def gem_choose_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """gtype_by_id | gtype_by_credentials → لیست بسته‌ها (پلن: همه)."""
    query = update.callback_query
    await query.answer()
    ptype = query.data.replace('gtype_', '')
    await _render_gem_list(query, ptype, 'all')


async def gem_filter_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """gp_{ptype}_{plan} → فیلتر لیست بر اساس نوع پلن."""
    query = update.callback_query
    await query.answer()
    _, ptype, plan = query.data.split('_', 2)
    await _render_gem_list(query, ptype, plan)


async def _render_gem_list(query, ptype, plan):
    gems = get_gems(purchase_type=ptype, plan_type=plan)
    plan_txt = 'همه' if plan == 'all' else PLAN_LABELS.get(plan, plan)
    head = (
        f"💎 *بسته‌های جم*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"روش خرید: *{PTYPE_TITLE[ptype]}*\n"
        f"نوع پلن: *{plan_txt}*\n"
    )
    if not gems:
        head += "\n❌ بسته‌ای در این فیلتر موجود نیست. فیلتر دیگه‌ای رو امتحان کن."
    else:
        head += "\nیه بسته انتخاب کن 👇"
    await query.edit_message_text(
        head, parse_mode='Markdown', reply_markup=gems_keyboard(gems, ptype, plan)
    )


async def show_gem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """gem_{id} → جزئیات بسته."""
    query = update.callback_query
    await query.answer()
    pk = int(query.data.split('_')[1])
    g = get_gem(pk)
    if not g:
        await query.edit_message_text("❌ بسته پیدا نشد.")
        return
    # g = Id, Title, Amount, BonusAmount, Price, OldPrice, PlanType, PurchaseType, Stock, IsAvailable
    total = g[2] + (g[3] or 0)
    bonus = f"  (+{g[3]} هدیه 🎁)" if g[3] else ""
    price_line = f"💰 *{g[4]:,} تومان*"
    if g[5] and g[5] > g[4]:
        off = round((g[5] - g[4]) / g[5] * 100)
        price_line = f"~~{g[5]:,}~~ ← 💰 *{g[4]:,} تومان*  🔥 {off}%-"

    ptype = g[7]
    method = PTYPE_TITLE.get(ptype, ptype)
    plan_txt = PLAN_LABELS.get(g[6], g[6])
    text = (
        f"💎 *{g[1]}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔢 مقدار: *{total:,} الماس*{bonus}\n"
        f"📦 نوع پلن: {plan_txt}\n"
        f"🚚 روش تحویل: {method}\n"
        f"{price_line}\n\n"
        f"_برای ادامه «ثبت سفارش» رو بزن_"
    )
    await query.edit_message_text(
        text, parse_mode='Markdown', reply_markup=gem_detail_keyboard(pk, ptype)
    )


# ═══════════════════════ گفتگوی ثبت سفارش جم ════════════════════════════════════
async def gem_buy_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """gbuy_{id} → شروع گرفتن اطلاعات اختصاصی بسته."""
    query = update.callback_query
    await query.answer()
    pk = int(query.data.split('_')[1])
    g = get_gem(pk)
    if not g:
        await query.edit_message_text("❌ بسته پیدا نشد.")
        return ConversationHandler.END

    total = g[2] + (g[3] or 0)
    ctx.user_data['gem_buy'] = {
        'pk': pk, 'title': g[1], 'price': g[4], 'total': total, 'purchase_type': g[7],
    }

    if g[7] == 'by_id':
        await query.edit_message_text(
            f"🆔 *ثبت سفارش — {g[1]}*\n"
            "━━━━━━━━━━━━━━━\n"
            "لطفاً *آیدی بازی (UID)* خودت رو بفرست.\n"
            "_(عددی که توی پروفایل فری‌فایرت نوشته شده)_",
            parse_mode='Markdown', reply_markup=gem_cancel_keyboard()
        )
        return GEM_UID
    else:
        await query.edit_message_text(
            f"🔐 *ثبت سفارش — {g[1]}*\n"
            "━━━━━━━━━━━━━━━\n"
            "اکانتت با چه روشی وارد می‌شه؟",
            parse_mode='Markdown', reply_markup=gem_login_method_keyboard()
        )
        return GEM_METHOD


async def gem_get_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.message.text.strip()
    if not uid.isdigit() or len(uid) < 5:
        await update.message.reply_text(
            "⚠️ آیدی بازی باید فقط عدد و معتبر باشه. دوباره بفرست:",
            reply_markup=gem_cancel_keyboard()
        )
        return GEM_UID
    ctx.user_data['gem_buy']['game_uid'] = uid
    return await _finalize_gem(update, ctx)


async def gem_get_method(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    method = query.data.replace('gm_', '')
    ctx.user_data['gem_buy']['login_method'] = method
    await query.edit_message_text(
        f"📧 روش ورود: *{METHOD_LABELS.get(method, method)}*\n\n"
        "حالا *ایمیل/یوزرنیم* ورود به اکانت رو بفرست:",
        parse_mode='Markdown', reply_markup=gem_cancel_keyboard()
    )
    return GEM_EMAIL


async def gem_get_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['gem_buy']['login_email'] = update.message.text.strip()
    await update.message.reply_text(
        "🔑 حالا *رمز عبور* اکانت رو بفرست:",
        parse_mode='Markdown', reply_markup=gem_cancel_keyboard()
    )
    return GEM_PASS


async def gem_get_pass(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['gem_buy']['login_password'] = update.message.text.strip()
    await update.message.reply_text(
        "🧩 اگه *کد بک‌آپ* داری بفرست، وگرنه این مرحله رو رد کن:",
        parse_mode='Markdown', reply_markup=gem_skip_keyboard()
    )
    return GEM_BACKUP


async def gem_get_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['gem_buy']['backup_code'] = update.message.text.strip()
    return await _finalize_gem(update, ctx)


async def gem_skip_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data['gem_buy']['backup_code'] = None
    return await _finalize_gem(update, ctx, via_query=query)


async def _finalize_gem(update, ctx, via_query=None):
    info = ctx.user_data.pop('gem_buy')
    ptype = info['purchase_type']
    if ptype == 'by_id':
        meta = {'purchase_type': 'by_id', 'game_uid': info['game_uid']}
    else:
        meta = {
            'purchase_type': 'by_credentials',
            'login_method': info.get('login_method', 'gmail'),
            'login_email': info.get('login_email'),
            'login_password': info.get('login_password'),
            'backup_code': info.get('backup_code'),
        }
    cart = cart_add(ctx, 'g', info['pk'], info['title'], info['price'], meta=meta, unique=True)

    text = (
        f"✅ *بسته به سبد اضافه شد!*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💎 {info['title']}\n"
        f"💰 {info['price']:,} تومان\n\n"
        f"🛒 سبد شما: *{len(cart)} آیتم* | جمع: *{_cart_total(cart):,} تومان*"
    )
    if via_query:
        await via_query.edit_message_text(text, parse_mode='Markdown',
                                          reply_markup=added_to_cart_keyboard())
    else:
        await update.message.reply_text(text, parse_mode='Markdown',
                                        reply_markup=added_to_cart_keyboard())
    return ConversationHandler.END


async def gem_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop('gem_buy', None)
    query = update.callback_query
    if query:
        await query.answer("لغو شد")
        await query.edit_message_text("✖️ ثبت سفارش لغو شد.", reply_markup=None)
        await query.message.reply_text("چه کاری برات بکنم؟", reply_markup=main_menu())
    else:
        await update.message.reply_text("✖️ لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END


def gem_conversation_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(gem_buy_start, pattern=r'^gbuy_\d+$')],
        states={
            GEM_UID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, gem_get_uid),
                CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$'),
            ],
            GEM_METHOD: [
                CallbackQueryHandler(gem_get_method, pattern=r'^gm_(gmail|facebook|vk)$'),
                CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$'),
            ],
            GEM_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, gem_get_email),
                CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$'),
            ],
            GEM_PASS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, gem_get_pass),
                CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$'),
            ],
            GEM_BACKUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, gem_get_backup),
                CallbackQueryHandler(gem_skip_backup, pattern='^gskip$'),
                CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$'),
            ],
        },
        fallbacks=[CallbackQueryHandler(gem_cancel, pattern='^gem_cancel$')],
        per_message=False,
    )
