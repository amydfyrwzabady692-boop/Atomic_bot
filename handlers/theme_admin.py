"""پنل اختصاصی مدیریت تم و ایموجی‌های پریمیوم (متن پیام‌ها و آیکون دکمه‌ها)."""
import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.ext import ContextTypes

from admin_notify import is_admin
from game import emoji, button_emoji
from bot.buttons import btn

_LOG = logging.getLogger(__name__)


def utf16_slice(text: str, offset: int, length: int) -> str:
    encoded = (text or "").encode("utf-16-le")
    start = max(0, int(offset) * 2)
    end = start + max(0, int(length) * 2)
    return encoded[start:end].decode("utf-16-le", errors="ignore")


def _extract_custom_emoji(message) -> tuple[str, str] | None:
    """(custom_emoji_id, placeholder) from a message containing a Premium custom emoji."""
    if not message:
        return None
    text = message.text or message.caption or ""
    entities = list(message.entities or []) + list(message.caption_entities or [])
    for ent in entities:
        if str(getattr(ent, "type", "")) in ("custom_emoji", "MessageEntityType.CUSTOM_EMOJI"):
            cid = str(getattr(ent, "custom_emoji_id", "") or "")
            if cid:
                placeholder = utf16_slice(text, ent.offset, ent.length) or "⭐"
                return cid, placeholder
    return None


def _extract_all_custom_emojis(message) -> list[tuple[str, str]]:
    """Extract all custom emoji entities in order for bulk auto-assign."""
    if not message:
        return []
    text = message.text or message.caption or ""
    entities = list(message.entities or []) + list(message.caption_entities or [])
    results = []
    for ent in entities:
        if str(getattr(ent, "type", "")) in ("custom_emoji", "MessageEntityType.CUSTOM_EMOJI"):
            cid = str(getattr(ent, "custom_emoji_id", "") or "")
            if cid:
                placeholder = utf16_slice(text, ent.offset, ent.length) or "⭐"
                results.append((cid, placeholder))
    return results


def _guard(update: Update) -> bool:
    user = update.effective_user
    return bool(user and is_admin(user.id))


async def _deny(update: Update):
    text = "⛔️ دسترسی فقط برای مدیر اصلی ربات مجاز است."
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.message:
        await update.message.reply_text(text)


def _main_hub_kb(target_type: str = "text") -> InlineKeyboardMarkup:
    # تب انتخاب متن پیام یا آیکون دکمه
    t_mark = "🔘 " if target_type == "text" else ""
    b_mark = "🔘 " if target_type == "btn" else ""
    type_row = [
        InlineKeyboardButton(f"{t_mark}📝 ایموجی‌های متن", callback_data="th_sw:text"),
        InlineKeyboardButton(f"{b_mark}🔘 آیکون دکمه‌ها", callback_data="th_sw:btn"),
    ]

    labels = emoji.CATEGORY_LABELS if target_type == "text" else button_emoji.BUTTON_CATEGORY_LABELS
    cat_rows = []
    for cat_id, cat_title in labels.items():
        cat_rows.append([
            InlineKeyboardButton(cat_title, callback_data=f"th_cat:{target_type}:{cat_id}")
        ])

    actions = [
        [InlineKeyboardButton("⚡ تخصیص خودکار دسته‌ای (Bulk Auto-Assign)", callback_data=f"th_bulk:{target_type}")],
        [InlineKeyboardButton("🔄 بروزرسانی حافظه کش", callback_data=f"th_ref:{target_type}")],
        [InlineKeyboardButton("🔙 پنل اصلی ادمین", callback_data="adm_home")],
    ]
    return InlineKeyboardMarkup([type_row] + cat_rows + actions)


def _category_kb(target_type: str, cat_id: str) -> InlineKeyboardMarkup:
    rows = []
    if target_type == "text":
        items = [(k, lbl, e) for k, (lbl, e, c) in emoji.EMOJI_DEFS.items() if c == cat_id]
        for k, lbl, def_e in items:
            themed = emoji.get_emoji(k)
            label = f"{lbl} ({def_e})"
            rows.append([InlineKeyboardButton(label, callback_data=f"th_k:text:{k}")])
    else:
        items = [(k, lbl, def_e) for k, (lbl, def_e, c) in button_emoji.BUTTON_EMOJI_DEFS.items() if c == cat_id]
        for k, lbl, _def_e in items:
            rows.append([btn(lbl, emoji_key=k, callback_data=f"th_k:btn:{k}")])

    rows.append([InlineKeyboardButton("🔙 بازگشت به دسته‌ها", callback_data=f"th_home:{target_type}")])
    return InlineKeyboardMarkup(rows)


def _item_kb(target_type: str, key: str, cat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 حذف و بازگشت به پیش‌فرض", callback_data=f"th_clr:{target_type}:{key}")],
        [InlineKeyboardButton("🔙 بازگشت به دسته", callback_data=f"th_cat:{target_type}:{cat_id}")],
    ])


async def theme_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دستور ورود به پنل تم ایموجی پریمیوم: /theme یا /emojis."""
    if not _guard(update):
        return await _deny(update)

    text = (
        "🎨 <b>مدیریت تم و ایموجی‌های پریمیوم تلگرام</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "در این بخش می‌توانید برای تمام متن‌ها و دکمه‌های ربات، ایموجی‌های پریمیوم تلگرام ست کنید.\n\n"
        "🔸 <b>ایموجی‌های متن</b>: داخل متن پیام‌ها به جای ایموجی‌های پیش‌فرض قرار می‌گیرند.\n"
        "🔸 <b>آیکون دکمه‌ها</b>: روی دکمه‌های شیشه‌ای اینلاین نمایش داده می‌شوند.\n\n"
        "👇 نوع و دسته‌بندی موردنظر را انتخاب کنید:"
    )
    kb = _main_hub_kb("text")
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def theme_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not _guard(update):
        return await _deny(update)

    data = query.data or ""
    await query.answer()

    if data.startswith("th_home:") or data == "th_home":
        target_type = data.split(":", 1)[1] if ":" in data else "text"
        ctx.user_data.pop("awaiting_theme_key", None)
        ctx.user_data.pop("awaiting_bulk_theme", None)
        text = (
            "🎨 <b>مدیریت تم و ایموجی‌های پریمیوم تلگرام</b>\n"
            "دسته‌بندی مورد نظر را انتخاب کنید:"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=_main_hub_kb(target_type))

    elif data.startswith("th_sw:"):
        target_type = data.split(":", 1)[1]
        text = (
            f"🎨 <b>بخش: {'📝 ایموجی‌های متن' if target_type == 'text' else '🔘 آیکون دکمه‌ها'}</b>\n"
            "دسته‌بندی مورد نظر را انتخاب کنید:"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=_main_hub_kb(target_type))

    elif data.startswith("th_ref:"):
        target_type = data.split(":", 1)[1]
        await asyncio.to_thread(emoji.refresh_cache)
        await asyncio.to_thread(button_emoji.refresh_cache)
        await query.answer("✅ حافظه کش ایموجی‌ها بروزرسانی شد.", show_alert=True)
        await query.edit_message_text(
            "🎨 <b>کش با موفقیت تازه شد!</b>\nدسته‌بندی مورد نظر را انتخاب کنید:",
            parse_mode="HTML",
            reply_markup=_main_hub_kb(target_type),
        )

    elif data.startswith("th_cat:"):
        _, target_type, cat_id = data.split(":", 2)
        ctx.user_data.pop("awaiting_theme_key", None)
        ctx.user_data.pop("awaiting_bulk_theme", None)
        labels = emoji.CATEGORY_LABELS if target_type == "text" else button_emoji.BUTTON_CATEGORY_LABELS
        cat_title = labels.get(cat_id, cat_id)
        text = (
            f"📂 <b>دسته: {cat_title}</b>\n"
            f"نوع: {'متن پیام‌ها' if target_type == 'text' else 'آیکون دکمه‌ها'}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "آیتم مورد نظر برای تغییر یا مشاهده را انتخاب کنید:"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=_category_kb(target_type, cat_id))

    elif data.startswith("th_k:"):
        _, target_type, key = data.split(":", 2)
        if target_type == "text":
            info = emoji.EMOJI_DEFS.get(key)
            if not info:
                return await query.answer("کلید یافت نشد.", show_alert=True)
            lbl, def_e, cat_id = info
            cur_html = emoji.get_emoji(key)
            is_themed = key in emoji._cache
            status = f"✅ ست شده: {cur_html}" if is_themed else f"⚪ پیش‌فرض: {def_e}"
        else:
            info = button_emoji.BUTTON_EMOJI_DEFS.get(key)
            if not info:
                return await query.answer("کلید یافت نشد.", show_alert=True)
            lbl, def_e, cat_id = info
            cid = button_emoji.get_button_icon(key)
            is_themed = cid is not None
            status = f"✅ آیکون ست شده (ID: <code>{cid}</code>)" if is_themed else f"⚪ پیش‌فرض: {def_e}"

        ctx.user_data["awaiting_theme_key"] = (target_type, key)
        ctx.user_data.pop("awaiting_bulk_theme", None)

        text = (
            f"🎨 <b>تنظیم ایموجی: {lbl}</b>\n"
            f"شناسه کلید: <code>{key}</code>\n"
            f"وضعیت فعلی: {status}\n"
            f"ایموجی یونیکد پیش‌فرض: {def_e}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✨ <b>روش تنظیم:</b>\n"
            "کافیست همین الان <b>یک ایموجی پریمیوم</b> به ربات ارسال کنید تا فوراً ذخیره شود.\n\n"
            "یا اگر می‌خواهید به حالت اولیه برگردد، دکمه حذف را بزنید."
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=_item_kb(target_type, key, cat_id))

    elif data.startswith("th_clr:"):
        _, target_type, key = data.split(":", 2)
        if target_type == "text":
            info = emoji.EMOJI_DEFS.get(key)
            cat_id = info[2] if info else "ui"
            await asyncio.to_thread(emoji.clear_emoji, key)
        else:
            info = button_emoji.BUTTON_EMOJI_DEFS.get(key)
            cat_id = info[2] if info else "nav"
            await asyncio.to_thread(button_emoji.clear_button_emoji, key)

        ctx.user_data.pop("awaiting_theme_key", None)
        await query.answer("🗑 به ایموجی پیش‌فرض بازگشت.", show_alert=True)
        text = f"✅ ایموجی کلید <code>{key}</code> حذف شد و به حالت پیش‌فرض بازگشت."
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=_category_kb(target_type, cat_id))

    elif data.startswith("th_bulk:"):
        target_type = data.split(":", 1)[1]
        ctx.user_data["awaiting_bulk_theme"] = target_type
        ctx.user_data.pop("awaiting_theme_key", None)

        registry = emoji.EMOJI_DEFS if target_type == "text" else button_emoji.BUTTON_EMOJI_DEFS
        keys = list(registry.keys())
        preview_list = "\n".join([f"{i+1}. <code>{k}</code> ({registry[k][0]})" for i, k in enumerate(keys[:15])])
        if len(keys) > 15:
            preview_list += f"\n... و {len(keys)-15} کلید دیگر"

        text = (
            "🚀 <b>تخصیص خودکار دسته‌ای (Bulk Auto-Assign)</b>\n"
            f"برای بخش: <b>{'📝 ایموجی‌های متن' if target_type == 'text' else '🔘 آیکون دکمه‌ها'}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "در این روش، کافیست یک پیام حاوی چندین ایموجی پریمیوم (به ترتیب کلیدها) برای ربات فوروارد کنید یا بفرستید.\n"
            "ربات به ترتیب هر ایموجی را به کلید متناظرش اختصاص می‌دهد.\n\n"
            f"<b>ترتیب کلیدها:</b>\n{preview_list}\n\n"
            "👇 پیام شامل پک ایموجی پریمیوم را بفرستید:"
        )
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 انصراف", callback_data=f"th_home:{target_type}")]
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=cancel_kb)


async def handle_theme_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """دریافت پیام تک یا دسته‌ای حاوی ایموجی پریمیوم از سمت ادمین.
    در صورت پردازش موفق، True برمی‌گرداند تا روت‌های دیگر هندل نکنند."""
    if not _guard(update) or not update.message:
        return False

    # بررسی حالت تک ایموجی
    if "awaiting_theme_key" in ctx.user_data:
        target_type, key = ctx.user_data["awaiting_theme_key"]
        extracted = _extract_custom_emoji(update.message)
        if not extracted:
            await update.message.reply_text(
                "⚠️ پیام شما حاوی ایموجی پریمیوم نبود!\n"
                "لطفاً یک ایموجی تلگرام پریمیوم از پنل ایموجی‌ها ارسال کنید.",
                parse_mode="HTML",
            )
            return True

        cid, placeholder = extracted
        if target_type == "text":
            await asyncio.to_thread(emoji.set_emoji, key, cid, placeholder)
            info = emoji.EMOJI_DEFS.get(key)
            cat_id = info[2] if info else "ui"
            preview_text = emoji.get_emoji(key)
            ctx.user_data.pop("awaiting_theme_key", None)
            reply_text = (
                f"✅ ایموجی <code>{key}</code> با موفقیت ذخیره شد!\n\n"
                f"پیش‌نمایش در متن پیام: {preview_text} <b>تست عنوان</b>\n"
                f"شناسه ایموجی: <code>{cid}</code>\n"
                f"جایگزین یونیکد: {placeholder}"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به دسته", callback_data=f"th_cat:text:{cat_id}")],
                [InlineKeyboardButton("🏠 منوی تم‌ها", callback_data="th_home:text")],
            ])
            await update.message.reply_text(reply_text, parse_mode="HTML", reply_markup=kb)
        else:
            await asyncio.to_thread(button_emoji.set_button_emoji, key, cid, placeholder)
            info = button_emoji.BUTTON_EMOJI_DEFS.get(key)
            cat_id = info[2] if info else "nav"
            ctx.user_data.pop("awaiting_theme_key", None)
            reply_text = (
                f"✅ آیکون دکمه <code>{key}</code> با موفقیت ذخیره شد!\n"
                f"شناسه ایموجی: <code>{cid}</code>\n"
                f"جایگزین یونیکد: {placeholder}\n\n"
                "👇 پیش‌نمایش زنده دکمه:"
            )
            kb = InlineKeyboardMarkup([
                [btn("نمونه زنده دکمه", emoji_key=key, callback_data="noop")],
                [InlineKeyboardButton("🔙 بازگشت به دسته", callback_data=f"th_cat:btn:{cat_id}")],
                [InlineKeyboardButton("🏠 منوی تم‌ها", callback_data="th_home:btn")],
            ])
            await update.message.reply_text(reply_text, parse_mode="HTML", reply_markup=kb)
        return True

    # بررسی حالت Bulk Auto-Assign
    if "awaiting_bulk_theme" in ctx.user_data:
        target_type = ctx.user_data.pop("awaiting_bulk_theme")
        emojis_found = _extract_all_custom_emojis(update.message)
        if not emojis_found:
            await update.message.reply_text(
                "⚠️ هیچ ایموجی پریمیومی در پیام ارسالی یافت نشد!\n"
                "لطفاً پیامی شامل ایموجی‌های پریمیوم ارسال کنید.",
                parse_mode="HTML",
            )
            return True

        registry = emoji.EMOJI_DEFS if target_type == "text" else button_emoji.BUTTON_EMOJI_DEFS
        keys = list(registry.keys())
        count = min(len(emojis_found), len(keys))

        def _bulk_save():
            for i in range(count):
                k = keys[i]
                cid, ph = emojis_found[i]
                if target_type == "text":
                    emoji.set_emoji(k, cid, ph)
                else:
                    button_emoji.set_button_emoji(k, cid, ph)

        await asyncio.to_thread(_bulk_save)
        report = (
            f"🎉 <b>تخصیص خودکار انجام شد!</b>\n"
            f"تعداد {count} ایموجی پریمیوم به ترتیب برای کلیدهای "
            f"<b>{'متن' if target_type == 'text' else 'دکمه‌ها'}</b> تنظیم و ذخیره شدند."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 مشاهده منوی تم‌ها", callback_data=f"th_home:{target_type}")]
        ])
        await update.message.reply_text(report, parse_mode="HTML", reply_markup=kb)
        return True

    return False
