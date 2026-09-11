from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.models import ButtonEmojiOverride

# key -> (label, default unicode emoji, category). Atomic Bot inline button definitions.
BUTTON_EMOJI_DEFS: dict[str, tuple[str, str, str]] = {
    # ناوبری
    "btn_back":          ("بازگشت",               "◀️", "nav"),
    "btn_home":          ("منوی اصلی",            "🏠", "nav"),
    "btn_next":          ("صفحه بعد",             "▶️", "nav"),
    "btn_prev":          ("صفحه قبل",             "◀️", "nav"),
    "btn_refresh":       ("بروزرسانی",            "🔄", "nav"),

    # منوی اصلی
    "btn_menu_ff":       ("محصولات فری‌فایر",     "🎮", "menu"),
    "btn_menu_wal":      ("کیف پول",              "💰", "menu"),
    "btn_menu_ord":      ("سفارش‌های من",         "📦", "menu"),
    "btn_menu_acc":      ("حساب من",              "👤", "menu"),
    "btn_menu_st":       ("فروشگاه اکانت",         "🛍", "menu"),
    "btn_menu_se":       ("پک سنس",               "🎯", "menu"),
    "btn_menu_stars":    ("خرید استارز",          "⭐", "menu"),
    "btn_menu_gc":       ("خرید گیفت کارت",       "🎁", "menu"),
    "btn_menu_su":       ("پشتیبانی",             "🎧", "menu"),
    "btn_menu_ref":      ("دعوت دوستان و جایزه",  "👥", "menu"),

    # تایید و انصراف
    "btn_confirm":       ("تایید و ادامه",        "✅", "action"),
    "btn_cancel":        ("انصراف",               "❌", "action"),
    "btn_buy":           ("خرید این بسته",        "💎", "action"),

    # روش‌های پرداخت
    "btn_pay_zp":        ("زرین‌پال",             "💳", "payment"),
    "btn_pay_card":      ("کارت‌به‌کارت",         "🏧", "payment"),
    "btn_pay_wal":       ("کیف پول (موجودی)",     "💰", "payment"),
    "btn_custom_amount": ("مبلغ دلخواه",          "✏️", "payment"),

    # دسته‌های محصولات
    "btn_gems_id":       ("جم با آیدی",           "🆔", "shop"),
    "btn_gems_cr":       ("جم با اطلاعات",        "🔐", "shop"),
    "btn_sense_pc":      ("پلتفرم PC",            "🖥", "shop"),
    "btn_sense_mob":     ("پلتفرم موبایل",        "📱", "shop"),

    # کلیدهای سازگاری با منوها و کیبورد (Appearance / Keyboards)
    "b.menu.ff":         ("محصولات فری‌فایر",     "🎮", "menu"),
    "b.menu.wal":        ("کیف پول",              "💰", "menu"),
    "b.menu.ord":        ("سفارش‌های من",         "📦", "menu"),
    "b.menu.acc":        ("حساب من",              "👤", "menu"),
    "b.menu.st":         ("فروشگاه اکانت",         "🛍", "menu"),
    "b.menu.se":         ("پک سنس",               "🎯", "menu"),
    "b.menu.stars":      ("خرید استارز",          "⭐", "menu"),
    "b.menu.gc":         ("خرید گیفت کارت",       "🎁", "menu"),
    "b.menu.su":         ("پشتیبانی",             "🎧", "menu"),
    "b.menu.ref":        ("دعوت دوستان و جایزه",  "👥", "menu"),

    "b.gems.id":         ("جم با آیدی",           "🆔", "shop"),
    "b.gems.cr":         ("جم با اطلاعات",        "🔐", "shop"),
    "b.nav.home":        ("منوی اصلی (بازگشت)",    "🔙", "nav"),
    "b.se.pc":           ("پلتفرم PC",            "🖥", "shop"),
    "b.se.mob":          ("پلتفرم موبایل",        "📱", "shop"),

    "b.gem.buy":         ("خرید این بسته جم",     "💎", "action"),
    "b.gem.ok":          ("تایید و ادامه پرداخت", "✅", "action"),
    "b.gem.no":          ("انصراف جم",            "❌", "action"),
    "b.stars.buy":       ("خرید بسته استارز",     "⭐", "action"),
    "b.stars.ok":        ("تایید استارز",         "✅", "action"),
    "b.stars.no":        ("انصراف استارز",        "❌", "action"),

    "b.pay.zp":          ("درگاه زرین‌پال",        "💳", "payment"),
    "b.pay.card":        ("کارت‌به‌کارت",         "🏧", "payment"),
    "b.pay.wal":         ("پرداخت با کیف پول",    "💰", "payment"),
    "b.wal.custom":      ("مبلغ دلخواه",          "✏️", "payment"),
    "b.wal.50":          ("۵۰ هزار تومان",        "💵", "payment"),
    "b.wal.100":         ("۱۰۰ هزار تومان",       "💵", "payment"),
    "b.wal.200":         ("۲۰۰ هزار تومان",       "💵", "payment"),
    "b.wal.500":         ("۵۰۰ هزار تومان",       "💵", "payment"),
}

BUTTON_ALIASES: dict[str, str] = {
    "b.menu.ff": "btn_menu_ff", "btn_menu_ff": "b.menu.ff",
    "b.menu.wal": "btn_menu_wal", "btn_menu_wal": "b.menu.wal",
    "b.menu.ord": "btn_menu_ord", "btn_menu_ord": "b.menu.ord",
    "b.menu.acc": "btn_menu_acc", "btn_menu_acc": "b.menu.acc",
    "b.menu.st": "btn_menu_st", "btn_menu_st": "b.menu.st",
    "b.menu.se": "btn_menu_se", "btn_menu_se": "b.menu.se",
    "b.menu.stars": "btn_menu_stars", "btn_menu_stars": "b.menu.stars",
    "b.menu.gc": "btn_menu_gc", "btn_menu_gc": "b.menu.gc",
    "b.menu.su": "btn_menu_su", "btn_menu_su": "b.menu.su",
    "b.menu.ref": "btn_menu_ref", "btn_menu_ref": "b.menu.ref",
    "b.gems.id": "btn_gems_id", "btn_gems_id": "b.gems.id",
    "b.gems.cr": "btn_gems_cr", "btn_gems_cr": "b.gems.cr",
    "b.se.pc": "btn_sense_pc", "btn_sense_pc": "b.se.pc",
    "b.se.mob": "btn_sense_mob", "btn_sense_mob": "b.se.mob",
    "b.nav.home": "btn_home", "btn_home": "b.nav.home",
    "b.gem.buy": "btn_buy", "btn_buy": "b.gem.buy",
    "b.gem.ok": "btn_confirm", "btn_confirm": "b.gem.ok",
    "b.gem.no": "btn_cancel", "btn_cancel": "b.gem.no",
    "b.stars.buy": "btn_buy",
    "b.stars.ok": "btn_confirm",
    "b.stars.no": "btn_cancel",
    "b.pay.zp": "btn_pay_zp", "btn_pay_zp": "b.pay.zp",
    "b.pay.card": "btn_pay_card", "btn_pay_card": "b.pay.card",
    "b.pay.wal": "btn_pay_wal", "btn_pay_wal": "b.pay.wal",
    "b.wal.custom": "btn_custom_amount", "btn_custom_amount": "b.wal.custom",
}

BUTTON_CATEGORY_LABELS: dict[str, str] = {
    "menu":    "📱 منوی اصلی",
    "nav":     "🧭 ناوبری",
    "action":  "⚡ عملیات و خرید",
    "payment": "💳 روش‌های پرداخت",
    "shop":    "🛍 دسته‌های فروشگاه",
}

BUTTON_EMOJI_KEYS = {k: f"{e} {lbl}" for k, (lbl, e, _c) in BUTTON_EMOJI_DEFS.items()}
BUTTON_DEFAULT_EMOJI = {k: e for k, (_l, e, _c) in BUTTON_EMOJI_DEFS.items()}
BUTTON_CATEGORY_OF = {k: c for k, (_l, _e, c) in BUTTON_EMOJI_DEFS.items()}

_cache: dict[str, ButtonEmojiOverride] = {}   # starts EMPTY, populated synchronously (async-safe)


def _get_model():
    from game.models import ButtonEmojiOverride
    return ButtonEmojiOverride


def refresh_cache() -> None:
    global _cache
    try:
        model = _get_model()
        _cache = {o.key: o for o in model.objects.all()}
    except Exception:
        _cache = {}


def get_button_icon(key: str) -> str | None:
    """Return themed custom_emoji_id for button key, checking overrides, aliases, and glyph coupling."""
    o = _cache.get(key)
    if o is not None:
        return o.custom_emoji_id
    alias = BUTTON_ALIASES.get(key)
    if alias:
        o = _cache.get(alias)
        if o is not None:
            return o.custom_emoji_id
    # Fallback: check glyph coupling if default emoji is known
    fb = BUTTON_DEFAULT_EMOJI.get(key) or (BUTTON_DEFAULT_EMOJI.get(alias) if alias else None)
    if fb:
        try:
            from game import emoji
            gid = emoji._glyph_map.get(emoji._norm_glyph(fb))
            if gid:
                return gid
        except Exception:
            pass
    return None


def get_button_label_emoji(key: str) -> str:
    alias = BUTTON_ALIASES.get(key)
    return BUTTON_DEFAULT_EMOJI.get(key) or (BUTTON_DEFAULT_EMOJI.get(alias) if alias else "")


def set_button_emoji(key: str, custom_emoji_id: str, placeholder: str) -> None:
    model = _get_model()
    model.objects.update_or_create(
        key=key,
        defaults={"custom_emoji_id": custom_emoji_id, "placeholder": placeholder}
    )
    refresh_cache()


def set_button_emojis_bulk(entries: list[tuple[str, str, str]]) -> int:
    """Bulk update ButtonEmojiOverride entries: list of (key, custom_emoji_id, placeholder)."""
    model = _get_model()
    count = 0
    to_create = []
    to_update = []
    existing = {o.key: o for o in model.objects.all()}
    for key, cid, ph in entries:
        if not key or not cid:
            continue
        if key in existing:
            o = existing[key]
            if o.custom_emoji_id != cid:
                o.custom_emoji_id = cid
                o.placeholder = ph
                to_update.append(o)
                count += 1
        else:
            to_create.append(model(key=key, custom_emoji_id=cid, placeholder=ph))
            count += 1
    if to_create:
        model.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        model.objects.bulk_update(to_update, ["custom_emoji_id", "placeholder"])
    refresh_cache()
    return count


def clear_button_emoji(key: str) -> bool:
    model = _get_model()
    deleted, _ = model.objects.filter(key=key).delete()
    refresh_cache()
    return deleted > 0


def list_button_overrides() -> list:
    model = _get_model()
    return list(model.objects.order_by("key"))
